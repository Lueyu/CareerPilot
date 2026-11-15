"""AgentAdapter: LLM 依赖的统一出口。

PRD: 所有 LLM 依赖必须有规则引擎降级。本文件提供:
- rule 模式(默认): 直接返回 None, 调用方走规则路径;
- deepseek/glm 模式: 通过 httpx 调 chat completions(设置 llm_provider + llm_api_key 后启用)。

典型用途: 面经提取、草稿答案生成、开放题评分增强。MVP 阶段全部走规则降级。
"""
from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings


class AgentAdapter:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def active(self) -> bool:
        return bool(self.settings.llm_provider and self.settings.llm_api_key)

    async def chat(self, system: str, user: str, json_mode: bool = False) -> dict[str, Any] | None:
        """返回 {"content": str} 或 None(降级信号)。"""
        if not self.active:
            return None
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return {"content": data["choices"][0]["message"]["content"]}
        except Exception:
            # 网络失败也降级到规则引擎, 不阻塞主流程(PRD 可靠性要求)
            return None


adapter = AgentAdapter()
