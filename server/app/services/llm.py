"""AgentAdapter: LLM 依赖的统一出口。

PRD: 所有 LLM 依赖必须有规则引擎降级。本文件提供:
- 规则模式(未配置): 直接返回 None, 调用方走规则路径;
- 已配置模式: OpenAI 兼容 /chat/completions(httpx)。

配置来源(运行时动态解析, 支持设置页在线修改, 无需重启):
1. DB AppSetting(key='llm') —— 设置页保存的运行时配置, 优先;
2. env(app/config.py Settings) —— 兜底默认。

未配置/供应商失败返回 None。货币预算或计费配置拒绝抛出策略异常，不进入供应商降级。
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import time
from typing import Any

import httpx
from sqlalchemy import select

from app.config import MAX_STUDY_TIMEOUT_SECONDS, get_settings
from app.services.llm_endpoint_config import resolve_llm_config
from app.services.llm_stream import ChatCompletionStream, StreamFailure
from app.services.model_outbound import ModelOutboundPolicy, OutboundPolicyError, model_client
from app.services.private_model_billing import BillingScope, BudgetPolicyError, reserve, settle
from app.services.secret_storage import SecretStorageError, open_setting

# Synchronous readiness hint only. Async calls always authenticate current storage.
_CACHE: dict[str, Any] | None = None

async def get_llm_config() -> dict[str, Any]:
    """返回 {provider, api_key, base_url, model, source}; source ∈ db|env|none。"""
    cfg = get_settings()
    merged = resolve_llm_config(cfg)
    try:
        from app.db import SessionLocal
        from app.models import AppSetting

        async with SessionLocal() as session:
            from app.services.recovery_control import require_normal
            await require_normal(session)
            row = (await session.execute(select(AppSetting).where(AppSetting.key == "llm"))).scalar_one_or_none()
        if row and isinstance(row.value, dict):
            merged = resolve_llm_config(cfg, open_setting("llm", row.value))
        elif row:
            raise SecretStorageError()
    except SecretStorageError:
        set_llm_config_cache({})
        raise
    except Exception:
        set_llm_config_cache({})
        raise SecretStorageError() from None
    set_llm_config_cache(merged)
    return merged


def set_llm_config_cache(value: dict[str, Any] | None) -> None:
    """Keep only boolean synchronous readiness hints, never a credential/config cache."""
    global _CACHE
    _CACHE = None if value is None else {"provider": bool(value.get("provider")), "api_key": bool(value.get("api_key"))}


class AgentAdapter:
    def __init__(self):
        # Per-instance diagnostic only; never retain provider bodies, URLs,
        # request headers, exception strings or credentials here.
        self.last_error: dict[str, Any] | None = None
        self.last_response_meta: dict[str, Any] = {}
        self.last_usage: dict[str, Any] | None = None

    async def _effective(self) -> dict[str, Any]:
        return await get_llm_config()

    @property
    def active(self) -> bool:
        """同步快捷判断: 缓存可用用缓存, 否则 env。保存配置后会 set_llm_config_cache 刷新。"""
        cfg = _CACHE
        if cfg is None:
            s = get_settings()
            return bool(s.llm_provider and s.llm_api_key)
        return bool(cfg.get("provider") and cfg.get("api_key"))

    async def chat_with(self, cfg: dict[str, Any], system: str, user: str, json_mode: bool = False,
                        timeout: float = 60.0, trace: Any = None, session: Any = None,
                        span_name: str | None = None, reasoning_effort: str | None = None,
                        *, stream: bool = False, total_timeout: float | None = None,
                        first_content_timeout: float | None = None,
                        on_content=None, on_reasoning=None, billing_scope: BillingScope | None = None) -> dict[str, Any] | None:
        """用给定配置调用一次 chat; 失败返回 None(降级信号)。

        trace+session 同时提供时, 记录 Agent Run span(provider/model/duration/tokens/cost/retry) 到观测台。
        私有请求先独立预留，再按冻结的配置价格结算；策略拒绝抛出 BudgetPolicyError。
        """
        self.last_error = None
        self.last_response_meta = {}
        self.last_usage = None
        if not (cfg.get("provider") and cfg.get("api_key") and cfg.get("base_url")):
            self.last_error = {"kind": "configuration"}
            return None
        if total_timeout is not None and (not stream or type(total_timeout) not in (int, float)
                or not 0 < total_timeout <= MAX_STUDY_TIMEOUT_SECONDS or not math.isfinite(total_timeout)):
            self.last_error = {"kind": "configuration"}
            return None
        if first_content_timeout is not None and (not stream or type(first_content_timeout) not in (int, float)
                or not 0 < first_content_timeout <= MAX_STUDY_TIMEOUT_SECONDS
                or not math.isfinite(first_content_timeout)):
            self.last_error = {"kind": "configuration"}
            return None
        headers = {"Authorization": f"Bearer {cfg['api_key']}"}
        payload: dict[str, Any] = {
            "model": cfg.get("model") or "",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
        }
        payload["max_tokens"] = cfg.get("output_limit", get_settings().private_llm_max_output_tokens)
        if billing_scope is None and trace is not None:
            billing_scope = BillingScope(trace.workspace_id, f"trace:{trace.id}:{span_name or 'llm'}")
        if cfg.get("thinking") in ("enabled", "disabled"):
            payload["thinking"] = {"type": cfg["thinking"]}
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if reasoning_effort is not None:
            # Per-call opt-in only. Ordinary scoring/generation callers keep
            # their existing payload and the user's configuration is unchanged.
            payload["reasoning_effort"] = reasoning_effort
        if stream:
            payload['stream'] = True
        t0 = time.perf_counter()
        retries = 0
        result: dict[str, Any] | None = None
        self.last_response_meta = {'transport_phase': 'request_not_yet_traced',
            'response_headers_received': False, 'response_body_complete': False,
            'transport_events': []}
        request_kind = 'UNKNOWN'

        async def record_transport_event(event: str, info: dict[str, Any]) -> None:
            nonlocal request_kind
            # The names come from HTTPcore, but still use a fixed allowlist.
            # Never copy/repr info: it contains URLs, headers, bodies and errors.
            parts = event.split('.')
            if len(parts) != 3:
                return
            protocol, operation, outcome = parts
            if protocol not in {'connection', 'proxy', 'http11', 'http2'} or outcome not in {'started', 'complete', 'failed'}:
                return
            if operation not in {'connect_tcp', 'connect_unix_socket', 'start_tls', 'retry',
                'send_connection_init', 'send_request_headers', 'send_request_body',
                'receive_response_headers', 'receive_response_body', 'receive_remote_settings',
                'response_closed', 'close'}:
                return
            is_http = protocol in {'http11', 'http2'}
            if is_http and outcome == 'started':
                # Inspect only the method to derive an allowlisted label; the
                # request object and every other info field remain transient.
                method = getattr(info.get('request'), 'method', None)
                if method in (b'CONNECT', 'CONNECT'):
                    request_kind = 'PROXY_CONNECT'
                elif method in (b'POST', 'POST'):
                    request_kind = 'TARGET_POST'
            kind = request_kind if is_http else 'UNKNOWN'
            events = self.last_response_meta['transport_events']
            if len(events) < 256:
                events.append({'event': event, 'elapsed_ms': int((time.perf_counter() - t0) * 1000),
                    'request_kind': kind})
            else:
                self.last_response_meta['transport_events_truncated'] = True
            # Cleanup traces must not hide the operation that actually failed.
            if operation not in {'response_closed', 'close'} and not (
                    self.last_response_meta.get('stream_done') and self.last_response_meta.get('response_body_complete')):
                self.last_response_meta['transport_phase'] = kind.lower() + ':' + event if is_http else event

        async def record_response_headers(response: httpx.Response) -> None:
            # HTTPX invokes this before consuming the body. Preserve only an
            # explicit safe projection even when the body later stalls/cancels.
            from app.services.project_autopilot_sources import retry_seconds, safe_request_id
            self.last_response_meta.update(status=response.status_code,
                transport_phase='reading_response_body', response_headers_received=True,
                response_headers_ms=int((time.perf_counter() - t0) * 1000))
            request_id = safe_request_id(response.headers.get('x-request-id') or response.headers.get('request-id'))
            if request_id:
                self.last_response_meta['request_id'] = request_id
            retry_after = retry_seconds(response.headers.get('retry-after'))
            if retry_after is not None:
                self.last_response_meta['retry_after'] = retry_after
            media_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
            if media_type:
                self.last_response_meta['content_type'] = media_type if media_type in {
                    'application/json', 'application/problem+json', 'text/plain', 'text/html',
                    'text/event-stream', 'application/octet-stream'} else 'other'

        # Policy errors happen outside provider-failure handling. No request exists yet.
        try:
            ModelOutboundPolicy.deployment().endpoint(cfg['base_url'])
        except OutboundPolicyError:
            self.last_error = {"kind": "outbound_policy", "code": "MODEL_OUTBOUND_POLICY"}
            raise
        try:
            call_id = await reserve(billing_scope, cfg, system, user, payload["max_tokens"])
        except BudgetPolicyError as exc:
            self.last_error = {"kind": "budget_policy", "code": exc.code, "retry_after": exc.retry_after}
            raise
        self.last_response_meta["billing_call_id"] = call_id
        # Provider deadlines and transport timings start after billing admission.
        t0 = time.perf_counter()
        charge = None
        billing_usage = None
        try:
            if stream:
                reader = ChatCompletionStream(self.last_response_meta, t0,
                                              first_content_timeout=first_content_timeout)
                try:
                    # One request, no fallback/reconnect. Bound the complete operation even
                    # while data/heartbeats arrive; per-I/O timeouts alone are insufficient.
                    async with asyncio.timeout(timeout if total_timeout is None else total_timeout):
                        async with model_client(cfg['base_url'], timeout=timeout, event_hooks={'response': [record_response_headers]}) as client:
                            async with client.stream('POST', f"{cfg['base_url'].rstrip('/')}/chat/completions",
                                headers=headers, json=payload, extensions={'trace': record_transport_event}) as resp:
                                resp.raise_for_status()
                                stream_result = await reader.read(resp, on_content, on_reasoning) if (on_content or on_reasoning) else await reader.read(resp)
                    result = stream_result
                    return result
                finally:
                    self.last_usage = reader.usage
                    billing_usage = reader.billing_usage
                    if getattr(reader, "reported_model", None):
                        self.last_response_meta["reported_model"] = reader.reported_model
            async with model_client(cfg['base_url'], timeout=timeout, event_hooks={'response': [record_response_headers]}) as client:
                resp = await client.post(
                    f"{cfg['base_url'].rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                    extensions={'trace': record_transport_event},
                )
                from app.services.project_autopilot_sources import safe_request_id
                self.last_response_meta.update(response_body_complete=True,
                    transport_phase='response_body_received',
                    response_body_ms=int((time.perf_counter() - t0) * 1000))
                resp.raise_for_status()
                data = resp.json()
                model_identity = safe_request_id(data.get('model'))
                if model_identity:
                    self.last_response_meta['reported_model'] = model_identity
                reported_usage = data.get('usage') or {}
                billing_usage = {k: reported_usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens') if k in reported_usage} if isinstance(reported_usage, dict) else None
                if isinstance(reported_usage, dict):
                    self.last_usage = {k: reported_usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                        if type(reported_usage.get(k)) is int and reported_usage[k] >= 0} or None
                if not self.last_response_meta.get('request_id'):
                    response_id = safe_request_id(data.get('id'))
                    if response_id:
                        self.last_response_meta['request_id'] = response_id
                finish = data['choices'][0].get('finish_reason')
                if finish in ('stop', 'length', 'content_filter', 'tool_calls', 'function_call'):
                    self.last_response_meta['finish_reason'] = finish
                content = data["choices"][0]["message"]["content"]
                if isinstance(content, str):
                    self.last_response_meta.update(output_chars=len(content), output_bytes=len(content.encode()),
                        output_sha256=hashlib.sha256(content.encode()).hexdigest())
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("invalid completion content")
                parsed_result = {"content": content}
                usage = data.get("usage") or {}
                if usage:
                    parsed_result["usage"] = {
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                    }
                result = parsed_result
                return result
        except OutboundPolicyError:
            self.last_error = {"kind": "outbound_policy", "code": "MODEL_OUTBOUND_POLICY"}
            raise
        except (httpx.TimeoutException, TimeoutError):
            self.last_error = {"kind": "timeout"}
            return None
        except httpx.HTTPStatusError as exc:
            self.last_error = {"kind": "http", "status": exc.response.status_code}
            self.last_error.update({key: self.last_response_meta[key] for key in ('request_id', 'retry_after') if key in self.last_response_meta})
            return None
        except httpx.RequestError:
            self.last_error = {"kind": "network"}
            return None
        except StreamFailure as exc:
            self.last_error = {"kind": exc.kind}
            return None
        except asyncio.CancelledError:
            # CancelledError derives from BaseException, so the generic handler
            # never sees it: the metric used to be recorded as a failure with no
            # reason at all. Classify it and preserve cancellation semantics.
            self.last_error = {"kind": "cancelled"}
            raise
        except Exception:
            self.last_error = {"kind": "invalid_response"}
            # Keep the existing None-return contract; callers decide whether a
            # fallback is valid for their operation.
            return None
        finally:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            # A stream that failed after the usage event still consumed tokens.
            # `result` stays None on failure, so fall back to the reader's last
            # validated usage instead of recording an invented zero.
            usage = (result or {}).get("usage") or self.last_usage or {}
            total_tokens = usage.get("total_tokens") or 0
            # Shield the durable settlement from caller cancellation. A crash still
            # leaves the committed reserve in place, never an automatic refund.
            settlement = asyncio.create_task(settle(call_id, billing_usage, has_output=bool((result or {}).get("content")) or "first_content_ms" in self.last_response_meta or bool(self.last_response_meta.get("output_chars"))))
            try:
                charge = await asyncio.shield(settlement)
            except BudgetPolicyError as exc:
                self.last_response_meta.update(billing_phase="settlement", billing_call_id=call_id, cost_basis="unknown")
                self.last_error = {"kind": "billing_settlement", "code": exc.code}
                exc.usage = {k: v for k, v in (billing_usage or {}).items() if type(v) is int and v >= 0}
                exc.request_meta = dict(self.last_response_meta)
                raise
            except asyncio.CancelledError:
                charge = await settlement
                raise
            cost = charge.actual_nano_cny / 1_000_000_000 if charge.actual_nano_cny is not None else None
            cost_basis = "configured_price" if cost is not None else "unknown"
            self.last_response_meta.update(cost_cny=cost, cost_basis=cost_basis, billing_state=charge.state)
            # ttft = time to the first visible token. Streamed calls expose
            # first_content_ms; a failed stream with no content stays NULL
            # rather than being replaced by the much earlier headers moment.
            # Non-stream calls only have the headers moment available.
            ttft_ms = (self.last_response_meta.get("first_content_ms") if stream
                       else self.last_response_meta.get("response_headers_ms"))
            if trace is not None and session is not None:
                try:
                    from app.services.tracing import add_span

                    detail = {
                        "agent": True,
                        "provider": cfg.get("provider"),
                        "model": cfg.get("model") or "",
                        "total_tokens": total_tokens,
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "retry": retries,
                        "ok": result is not None,
                        "cost_note": cost_basis,
                        "billing_call_id": call_id,
                    }
                    add_span(session, trace, span_name or f"llm:{cfg.get('model') or 'unknown'}",
                             duration_ms=duration_ms, cost_cny=cost or 0.0, detail=detail,
                             status="OK" if result is not None else "ERROR")
                except Exception:  # span 记录失败不影响主流程
                    pass
            # Caller-facing last_error keeps its coarse `kind` contract: failover
            # and user diagnostics read it. Only the persisted metric carries the
            # finer SSE code, so every failure has an actionable reason. Codes
            # are local identifiers authored in llm_stream.py, never provider data.
            metric_error = (self.last_error or {}).get("kind")
            if result is None and not metric_error:
                metric_error = "unclassified"
            stream_code = self.last_response_meta.get("stream_error")
            if (metric_error and isinstance(stream_code, str)
                    and stream_code.isidentifier() and len(stream_code) <= 32):
                metric_error = f"{metric_error}:{stream_code}"
            # 工业级观测: 每次调用一行指标(尽力而为, 不阻断主流程)。
            try:
                await self._record_metric(
                    kind=span_name or "llm",
                    provider=cfg.get("provider"),
                    model=cfg.get("model"),
                    duration_ms=duration_ms,
                    ttft_ms=ttft_ms,
                    usage=usage,
                    ok=result is not None,
                    error_kind=metric_error,
                    finish_reason=self.last_response_meta.get("finish_reason"),
                    stream=bool(stream),
                    session=session, cost_cny=cost, cost_basis=cost_basis,
                    billing_call_id=call_id, workspace_id=billing_scope.workspace_id if billing_scope else None,
                )
            except Exception:
                pass

    async def _record_metric(self, *, kind: str, provider: str | None, model: str | None,
                             duration_ms: int, ttft_ms: int | None, usage: dict,
                             ok: bool, error_kind: str | None, finish_reason: str | None,
                             stream: bool, session: Any | None, cost_cny: float | None,
                             cost_basis: str, billing_call_id: str, workspace_id: int | None) -> None:
        """写入 agent_metrics 一行。优先复用调用方会话; 否则开独立会话提交。"""
        from app.models.metric import AgentMetric

        metric = AgentMetric(
            kind=(kind or "llm")[:48],
            provider=(provider or "")[:48] or None,
            model=(model or "")[:120] or None,
            duration_ms=int(duration_ms),
            ttft_ms=int(ttft_ms) if ttft_ms is not None else None,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens") or 0,
            cost_cny=cost_cny, cost_basis=cost_basis, billing_call_id=billing_call_id, workspace_id=workspace_id,
            ok=bool(ok),
            error_kind=(error_kind or "")[:48] or None,
            finish_reason=(finish_reason or "")[:32] or None,
            stream=bool(stream),
        )
        if session is not None:
            session.add(metric)
            return
        from app.db import SessionLocal

        async with SessionLocal() as own:
            own.add(metric)
            await own.commit()

    async def chat(self, system: str, user: str, json_mode: bool = False, timeout: float = 60.0,
                   trace: Any = None, session: Any = None, span_name: str | None = None,
                   billing_scope: BillingScope | None = None) -> dict[str, Any] | None:
        """返回 {"content": str} 或 None(降级信号)。配置动态解析(设置页在线修改即时生效)。

        trace+session 提供时记录 Agent Run span(provider/model/tokens/cost) 到观测台。
        """
        cfg = await self._effective()
        return await self.chat_with(cfg, system, user, json_mode=json_mode, timeout=timeout,
                                    trace=trace, session=session, span_name=span_name, billing_scope=billing_scope)


adapter = AgentAdapter()
