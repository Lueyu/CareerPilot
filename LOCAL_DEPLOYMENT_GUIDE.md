# CareerPilot 本地部署指南

> 本地优先（local-first）：所有私人数据（答卷、面经、记忆、能力画像）只存在你自己的 PostgreSQL 里；
> 对外发布走 GitHub 静态门户，二者完全隔离。不需要任何云服务器。

## 方式 A：Docker Compose（推荐）

前置条件：[Docker Desktop](https://www.docker.com/products/docker-desktop/)（含 compose v2）。

```powershell
# 1. 准备环境变量（可选：填入 LLM / GitHub Token）
cp .env.example .env

# 2. 构建并启动（首次约 3-5 分钟）
docker compose up -d --build

# 3. 打开
#    前端  http://localhost:5173
#    API   http://localhost:8000/api/v1/docs   (Swagger)
```

服务组成：

| 容器 | 端口 | 说明 |
|---|---|---|
| careerpilot-db | 5432 | PostgreSQL 16，数据落卷 `pgdata` |
| careerpilot-api | 8000 | FastAPI 后端（启动时自动建表+种子+幂等迁移） |
| careerpilot-web | 5173 | Nginx 托管的前端静态站（构建时打包题库） |

常用命令：

```powershell
docker compose logs -f server      # 看后端日志
docker compose down                # 停止（数据保留）
docker compose down -v             # 停止并删除数据卷（谨慎）
docker compose up -d --build       # 改代码后重建
```

## 方式 B：本地裸跑（开发模式）

前置条件：Python 3.12+、Node 20+、PostgreSQL 16（本地服务即可）。

```powershell
# 1. 数据库（任选其一）
#    a) 已有本地 PostgreSQL: 创建库与用户
#    b) 只起 DB 容器: docker run -d --name careerpilot-db -p 5432:5432 `
#         -e POSTGRES_USER=careerpilot -e POSTGRES_PASSWORD=careerpilot_dev `
#         -e POSTGRES_DB=careerplot postgres:16-alpine

# 2. 后端
cd server
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy ..\.env.example .env          # 按需修改（LLM / GitHub Token）
python migrate.py                  # 幂等 SQL 迁移（可重复执行）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
#    首次启动自动 create_all + 种子数据（8 能力域 / 公开题库映射 / 演示工作空间）

# 3. 前端
cd ..\web
npm install
npm run dev                        # http://localhost:5173
```

默认鉴权：`Authorization: Bearer cp-dev-token`（`.env` 里 `API_TOKEN` 可改）。
前端默认指向 `http://127.0.0.1:8000/api/v1`，可用 `VITE_API_BASE` 环境变量覆盖。

## 数据备份与恢复

```powershell
.\deploy\backup.ps1                       # 备份到 .\backups\careerpilot-时间戳.sql
.\deploy\restore.ps1 .\backups\xxx.sql    # 恢复（会覆盖现有数据，脚本内有 5 秒确认）
```

脚本自动检测 `careerpilot-db` 容器（Docker 场景免装本机 psql），否则回退到本机 `pg_dump`。
连接参数可用 `PGHOST / PGPORT / PGUSER / PGDATABASE / PGPASSWORD` 覆盖。

## 公开门户（可选，独立于私人数据）

`portal/` 是 agent-interview.icu 的静态站源码，构建产物发布到 OSS，与本应用的数据完全隔离：

```powershell
node portal/build.mjs        # 用 portal/content 下的 Markdown 生成 portal/dist
```

在应用内「项目与职业资产」页发起的发布流水线，会把审核通过的内容以 PR 形式
提交到 `PORTAL_TARGET_REPO`（默认 `Lueyu/agent-interview` 的 `portal/content`），
`GITHUB_TOKEN` 未配置时发布功能自动停用（页面会诚实标注）。

## 升级

```powershell
git pull
docker compose up -d --build     # 启动时自动跑 migrate.py（幂等，可重复执行）
```

数据库变更遵循「只加不改删」的幂等迁移约定（`server/migrations/*.sql`），
本地单机场景不使用 Alembic 版本表。
