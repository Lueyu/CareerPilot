"""一次性迁移: 职业画像 P5 → P6(阿里 P6 对标)。

把现有 demo 库的岗位标准、目标等级、记忆文案从 P5 升级到 P6。
幂等: 已 P6 的 standard 跳过; 重复跑安全。
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db import engine  # noqa: F401  (触发 Base 模型注册通过 app.db)

P6_TARGETS = {
    "agent": 4.5,
    "tools": 4.0,
    "memory": 4.0,
    "rag": 4.0,
    "eval": 5.0,
    "backend": 4.5,
    "reliability": 4.0,
    "deploy": 3.5,
}


async def main() -> None:
    async with engine.begin() as conn:
        # 1. 岗位标准: level P5→P6, version v1.0→v1.1; targets 目标等级更新
        res = await conn.execute(
            text("SELECT id FROM job_standards WHERE level != 'P6' OR version != 'v1.1'")
        )
        std_ids = [row[0] for row in res]
        for sid in std_ids:
            await conn.execute(
                text("UPDATE job_standards SET level='P6', version='v1.1' WHERE id=:id"),
                {"id": sid},
            )
            for key, lv in P6_TARGETS.items():
                await conn.execute(
                    text(
                        "UPDATE job_standard_targets SET target_level=:lv "
                        "WHERE standard_id=:sid AND skill_id=(SELECT id FROM skills WHERE key=:key)"
                    ),
                    {"lv": lv, "sid": sid, "key": key},
                )

        # 2. 工作空间目标等级
        res = await conn.execute(
            text("UPDATE workspaces SET target_level='P6' WHERE target_level IS NULL OR target_level='P5'")
        )

        # 3. 记忆: CareerGoal 文案升级
        await conn.execute(
            text(
                "UPDATE memory_items SET content='目标: 2026 年内拿到 AI Agent 应用开发工程师 P6 offer, 薪资目标 65-75 万' "
                "WHERE mtype='CareerGoal' AND content LIKE '%P5%'"
            )
        )

        print(f"[migrate] job_standards updated: {len(std_ids)}")
        print("[migrate] workspaces / memory_items updated")


if __name__ == "__main__":
    asyncio.run(main())