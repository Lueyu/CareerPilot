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
from typing import Any

import httpx
from sqlalchemy import select

from app.config import get_settings

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
                        timeout: float = 60.0) -> dict[str, Any] | None:
        """用给定配置调用一次 chat; 失败返回 None(降级信号)。"""
        if not (cfg.get("provider") and cfg.get("api_key") and cfg.get("base_url")):
            return None
        headers = {"Authorization": f"Bearer {cfg['api_key']}"}
        payload: dict[str, Any] = {
            "model": cfg.get("model") or "",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{cfg['base_url'].rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return {"content": data["choices"][0]["message"]["content"]}
        except Exception:
            # 网络失败也降级到规则引擎, 不阻塞主流程(PRD 可靠性要求)
            return None

    async def chat(self, system: str, user: str, json_mode: bool = False) -> dict[str, Any] | None:
        """返回 {"content": str} 或 None(降级信号)。配置动态解析(设置页在线修改即时生效)。"""
        cfg = await self._effective()
        return await self.chat_with(cfg, system, user, json_mode=json_mode)


adapter = AgentAdapter()
