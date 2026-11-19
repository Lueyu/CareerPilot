"""AgentAdapter: LLM 依赖的统一出口。

PRD: 所有 LLM 依赖必须有规则引擎降级。本文件提供:
- 规则模式(未配置): 直接返回 None, 调用方走规则路径;
- 已配置模式: OpenAI 兼容 /chat/completions(httpx)。

配置来源(运行时动态解析, 支持设置页在线修改, 无需重启):
1. DB AppSetting(key='llm') —— 设置页保存的运行时配置, 优先;
2. env(app/config.py Settings) —— 兜底默认。

所有取值失败/网络失败 → 返回 None 降级到规则引擎, 不阻塞主流程(PRD 可靠性要求)。
"""
from __future__ import annotations

import time
import hashlib
import asyncio
import math
from typing import Any

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.services.llm_stream import ChatCompletionStream, StreamFailure

# 生效配置缓存(TTL 内不再查库; 设置页保存后立即 refresh)
_CACHE: dict[str, Any] | None = None
_CACHE_AT: float = 0.0
_TTL = 10.0  # seconds


async def get_llm_config() -> dict[str, Any]:
    """返回 {provider, api_key, base_url, model, source}; source ∈ db|env|none。"""
    global _CACHE, _CACHE_AT
    now = time.monotonic()
    if _CACHE is not None and (now - _CACHE_AT) < _TTL:
        return _CACHE

    cfg = get_settings()
    merged: dict[str, Any] = {
        "provider": cfg.llm_provider or "",
        "api_key": cfg.llm_api_key or "",
        "base_url": cfg.llm_base_url or "",
        "model": cfg.llm_model or "",
        "source": "env" if (cfg.llm_provider and cfg.llm_api_key) else "none",
    }
    try:
        from app.db import SessionLocal
        from app.models import AppSetting

        async with SessionLocal() as session:
            row = (await session.execute(select(AppSetting).where(AppSetting.key == "llm"))).scalar_one_or_none()
        if row and isinstance(row.value, dict):
            dbv = row.value
            for k in ("provider", "api_key", "base_url", "model"):
                if dbv.get(k):
                    merged[k] = dbv[k]
            if merged["provider"] and merged["api_key"]:
                merged["source"] = "db"
    except Exception:
        pass  # DB 读取失败 → 用 env 兜底
    _CACHE = merged
    _CACHE_AT = now
    return merged


def set_llm_config_cache(value: dict[str, Any] | None) -> None:
    """设置页保存/清除配置后立即刷新缓存。"""
    global _CACHE, _CACHE_AT
    _CACHE = value
    _CACHE_AT = time.monotonic()


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
                        on_content=None) -> dict[str, Any] | None:
        """用给定配置调用一次 chat; 失败返回 None(降级信号)。

        trace+session 同时提供时, 记录 Agent Run span(provider/model/duration/tokens/cost/retry) 到观测台。
        cost 按 ¥2/百万 token 估算(网关计价未知, 仅作量级参考, 记录在 detail 里注明 estimate)。
        """
        self.last_error = None
        self.last_response_meta = {}
        self.last_usage = None
        if not (cfg.get("provider") and cfg.get("api_key") and cfg.get("base_url")):
            self.last_error = {"kind": "configuration"}
            return None
        if total_timeout is not None and (not stream or type(total_timeout) not in (int, float)
                or not 0 < total_timeout <= 600 or not math.isfinite(total_timeout)):
            self.last_error = {"kind": "configuration"}
            return None
        headers = {"Authorization": f"Bearer {cfg['api_key']}"}
        payload: dict[str, Any] = {
            "model": cfg.get("model") or "",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
        }
        if cfg.get("output_limit"):
            payload["max_tokens"] = cfg["output_limit"]
        if stream and cfg.get("report_usage"):
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
            from app.services.project_autopilot_sources import safe_request_id, retry_seconds
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

        try:
            if stream:
                reader = ChatCompletionStream(self.last_response_meta, t0)
                try:
                    # One request, no fallback/reconnect. Bound the complete operation even
                    # while data/heartbeats arrive; per-I/O timeouts alone are insufficient.
                    async with asyncio.timeout(timeout if total_timeout is None else total_timeout):
                        async with httpx.AsyncClient(timeout=timeout, event_hooks={'response': [record_response_headers]}) as client:
                            async with client.stream('POST', f"{cfg['base_url'].rstrip('/')}/chat/completions",
                                headers=headers, json=payload, extensions={'trace': record_transport_event}) as resp:
                                resp.raise_for_status()
                                stream_result = await reader.read(resp, on_content) if on_content else await reader.read(resp)
                    result = stream_result
                    return result
                finally:
                    self.last_usage = reader.usage
                    if getattr(reader, "reported_model", None):
                        self.last_response_meta["reported_model"] = reader.reported_model
            async with httpx.AsyncClient(timeout=timeout, event_hooks={'response': [record_response_headers]}) as client:
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
        except Exception:
            self.last_error = {"kind": "invalid_response"}
            # Keep the existing None-return contract; callers decide whether a
            # fallback is valid for their operation.
            return None
        finally:
            if trace is not None and session is not None:
                try:
                    from app.services.tracing import add_span

                    duration_ms = int((time.perf_counter() - t0) * 1000)
                    usage = (result or {}).get("usage") or {}
                    total_tokens = usage.get("total_tokens") or 0
                    detail = {
                        "agent": True,
                        "provider": cfg.get("provider"),
                        "model": cfg.get("model") or "",
                        "total_tokens": total_tokens,
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "retry": retries,
                        "ok": result is not None,
                        "cost_note": "estimate ¥2/M tokens",
                    }
                    cost = round(total_tokens * 2e-6, 6)
                    add_span(session, trace, span_name or f"llm:{cfg.get('model') or 'unknown'}",
                             duration_ms=duration_ms, cost_cny=cost, detail=detail,
                             status="OK" if result is not None else "ERROR")
                except Exception:  # span 记录失败不影响主流程
                    pass

    async def chat(self, system: str, user: str, json_mode: bool = False, timeout: float = 60.0,
                   trace: Any = None, session: Any = None, span_name: str | None = None) -> dict[str, Any] | None:
        """返回 {"content": str} 或 None(降级信号)。配置动态解析(设置页在线修改即时生效)。

        trace+session 提供时记录 Agent Run span(provider/model/tokens/cost) 到观测台。
        """
        cfg = await self._effective()
        return await self.chat_with(cfg, system, user, json_mode=json_mode, timeout=timeout,
                                    trace=trace, session=session, span_name=span_name)


adapter = AgentAdapter()
