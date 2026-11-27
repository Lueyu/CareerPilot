"""幂等迁移执行器(单机本地版, 替代 alembic)。

用法:
    python migrate.py            # 顺序执行 migrations/*.sql(全部幂等, 可重复跑)

约定:
- 每个 .sql 文件必须是幂等的(IF NOT EXISTS / ADD COLUMN IF NOT EXISTS);
- 执行器不做版本表记账(单机简单优先), 重复执行零副作用;
- 新表由 app.main lifespan 的 create_all 创建; 本执行器负责新列/索引/数据修补。
"""
import asyncio
import sys
from pathlib import Path

import asyncpg

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _sync_dsn() -> str:
    """从 .env / 环境变量取 database_url, 转成 asyncpg 直连 DSN。"""
    settings = get_settings()
    settings.require_secure_personal_startup()
    url = settings.database_url
    # postgresql+asyncpg://user:pass@host:port/db -> postgresql://user:pass@host:port/db
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    # asyncpg 不认 sqlalchemy 风格 query 参数(如有)
    return dsn.split("?")[0]


async def run_migration(dsn: str, path: Path) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        sql = path.read_text(encoding="utf-8")
        await conn.execute(sql)
        print(f"[ok] {path.name}")
    finally:
        await conn.close()


async def main() -> int:
    dsn = _sync_dsn()
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print("no migration files found")
        return 0
    print(f"target dsn: {dsn.split('@')[-1]}")
    for f in files:
        try:
            await run_migration(dsn, f)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {f.name}: {exc}")
            return 1
    print(f"done: {len(files)} migration(s) applied (idempotent)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
