"""容器启动引导: 先 create_all 再交给 migrate.py。

- 全新数据库: create_all 直接按当前模型建出完整 schema。
- 旧数据库: create_all 只补缺失的表; 缺失的列由 migrations/*.sql(ADD COLUMN IF NOT EXISTS)补齐。
全部幂等, 可重复执行。
"""
from __future__ import annotations

import asyncio
import sys

from app import personal_startup as _personal_startup  # noqa: F401 (before database/model imports)

# isort: split
import app.models  # noqa: F401  确保全部模型注册进 Base.metadata
from app.db import Base, engine


async def main() -> None:
    from app.config import get_settings
    get_settings().require_secure_personal_startup()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
    print("[bootstrap] create_all done.")
