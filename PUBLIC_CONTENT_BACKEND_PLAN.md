# CareerPilot 公开内容发布后端 · 文件级实施计划

> 依据 `CareerPilot_UI_Review_and_Public_Portal_v3_1.md` Part C/E/F。
> 原则: 增量实现、真实 GitHub 发布(不用 Mock 冒充)、本地部署不变(不加 ECS/RDS/新后端服务)。

---

## 0. 审计结论(现有后端可复用资产)

| 资产 | 位置 | 复用方式 |
|---|---|---|
| Workspace 隔离 | `app/api/deps.py` `get_workspace` | 新路由全部复用, 查询强制 `workspace_id` 过滤 |
| 认证 | `app/api/deps.py` `require_auth` | 复用(token 校验) |
| Trace/审计 | `app/services/tracing.py` + `app/models/trace.py` | 发布全程 `start_trace`→`add_span`→`finish_trace`+`emit_event`; 审批写 `DecisionRecord` |
| 导入流水线模板 | `app/api/v1/bank.py` `_run_import` | 短码编号/幂等查重/降级模式照搬 |
| LLM 降级 | `app/services/llm.py` `AgentAdapter` | 内容摘要/事实核查可选 LLM, 默认规则 |
| 建表 | `app/main.py` lifespan `create_all` | 新表重启即建, 无需迁移脚本 |
| 静态站核对物 | `portal/content/` + `portal/build.mjs` | 发布产物的目录结构/文件命名对齐此 |

**关键现状**: 无 `.env`(全部默认值); 无 GitHub 依赖(发布用已有 `httpx` 直调 REST, 不新增 PyGithub)。

---

## 1. 目标仓库与发布拓扑(需用户确认)

- 独立内容仓库 `TARGET_REPO`(形如 `owner/repo`): 存放 agent-interview.icu 门户源内容(与 portal/content/ 同构)。
- 发布 = 在该仓库建 `publish/<slug>-<ts>` 分支 → 写入 Markdown/JSON → 开 PR → 追踪 Actions 构建 → (merge 后部署 OSS)。
- 回滚 = 基于上一次 `PUBLISHED` 提交, 建 revert PR 恢复内容。

见 §7「待确认项」——仓库地址与 token 未定前, 开发不阻塞(全部走配置注入)。

---

## 2. 新增配置 `server/app/config.py`(修改)

在 `Settings` 追加(全部可空, 空 = 该能力禁用并明确报错, 不静默 mock):

```python
# 公开内容发布(GitHub)
github_token: str = ""            # PAT, 需 repo + workflow 权限
github_api_url: str = "https://api.github.com"
portal_target_repo: str = ""      # "owner/repo" — 独立内容仓库
portal_target_branch: str = "main"   # PR 目标分支(即内容仓库主分支)
portal_base_branch: str = "main"     # 分支 fork 起点
portal_auto_merge: bool = False   # 低风险自动 merge 开关(默认关, 需用户授权)
```

---

## 3. 新增模型 `server/app/models/public_content.py`(新建)

对齐 Part C1 五表。全部 `workspace_id` 外键 CASCADE。

### 3.1 `PublicContentSource` → `public_content_sources`
内容来源登记(每条被引用的来源必须先入库, 记录版权)。

- `id`, `short_code`(unique, `SRC-<n>`), `workspace_id` FK
- `kind`(URL / REPO / ORIGINAL / DOC / GENERATED)
- `title`, `url`(nullable), `license_name`(nullable), `license_url`(nullable)
- `license_status`(`UNKNOWN`/`CLEARED`/`BLOCKED`, 默认 UNKNOWN)
- `content_hash`(去重), `retrieved_at`, `meta` JSONB, `created_at`

### 3.2 `PublicContentDraft` → `public_content_drafts`
核心草稿表。

- `id`, `short_code`(unique, `DRAFT-<n>`), `workspace_id` FK
- `title`, `slug`(workspace 内唯一, `UniqueConstraint(workspace_id, slug)`)
- `category`(`learning-path`/`guide`/`interview-question`/`hands-on-lab`/`case-study`/`changelog`)
- `markdown_body` Text(含 frontmatter 的完整 md 或纯 body)
- `frontmatter` JSONB(tags/level/estimated_reading_minutes/published_at/author/canonical_url/…)
- `source_ids` JSONB(引用 `PublicContentSource.id`)
- `license_status`(`UNKNOWN`/`CLEARED`/`BLOCKED`)
- `privacy_scan_status`(`PENDING`/`CLEAN`/`FLAGGED`)
- `fact_check_status`(`PENDING`/`PASSED`/`FLAGGED`)
- `approval_status`(`DRAFT`/`NEEDS_REVIEW`/`APPROVED`/`REJECTED`)
- `status`(发布状态机, §4)
- `target_repo`, `target_branch`(覆盖全局配置)
- `published_commit_sha`、`published_url`、`rollback_commit_sha`(nullable)
- `idempotency_key`(unique, 幂等闭环)
- `created_at`, `updated_at`(onupdate)

### 3.3 `ContentPublication` → `content_publication_runs`
每次发布运行(草稿可多次发布/回滚, 历史完整)。

- `id`, `short_code`(unique, `PUB-<n>`), `workspace_id` FK, `draft_id` FK
- `status`(`PR_CREATED`/`BUILDING`/`PUBLISHED`/`ROLLED_BACK`/`VALIDATION_FAILED`/`BUILD_FAILED`/`REJECTED`)
- `head_branch`, `base_branch`, `pr_number`, `pr_url`, `commit_sha`
- `workflow_run_id`(Actions run id), `workflow_name`, `build_status`(pending/success/failure/cancelled/timed_out)
- `published_at`, `rolled_back_at`(nullable)
- `idempotency_key`(unique), `meta` JSONB, `created_at`

### 3.4 `ContentPublicationRule` → `content_publication_rules`
用户级自动发布规则(Part E 低风险才可自动)。

- `id`, `workspace_id` FK(每 workspace 一套)
- `auto_publish_low_risk`(bool, 默认 False)
- `auto_merge`(bool, 默认 False)
- `require_fact_check`(bool, 默认 True)
- `require_privacy_scan`(bool, 默认 True)
- `require_approval`(bool, 默认 True)
- `blocked_categories` JSONB(默认空)
- `updated_at`

### 3.5 `ContentReleaseAudit` → `content_release_audit`
不可变审计日志(每个动作一条, 含审批记录副本)。

- `id`, `workspace_id` FK, `draft_id` FK(nullable), `publication_id` FK(nullable)
- `action`(`validate`/`approve`/`reject`/`publish`/`rollback`/`export`)
- `actor`(token 来源记 `"api-token"`), `summary`, `detail` JSONB, `created_at`

### 3.6 注册(`server/app/models/__init__.py` 修改)
在 import 段与 `__all__` 追加五个类。

---

## 4. 发布状态机(对齐 Part C2)

```
DRAFT ─validate─▶ VALIDATING ─▶ NEEDS_REVIEW ─approve─▶ APPROVED
                  │(任一不过)         │(拒绝)              │publish
                  ▼                   ▼                    ▼
           VALIDATION_FAILED     REJECTED           PR_CREATED ─▶ BUILDING ─▶ PUBLISHED
                                                                        │ build 失败
                                                                        ▼
                                                                   BUILD_FAILED
PUBLISHED ─rollback─▶ ROLLED_BACK
```

落库规则:
- `status` 存草稿当前态; `publication.status` 存该次运行态。
- 校验任一不过 → `VALIDATION_FAILED`(可修正后重 validate)。
- `APPROVED` 才能 publish; publish 后回滚须存在上一个 `PUBLISHED` 运行(否则 409)。

---

## 5. 校验服务 `server/app/services/content_validation.py`(新建)

纯函数/规则, 不依赖网络(链接检查除外, 失败降级为警告)。

### 5.1 隐私扫描 `privacy_scan(md, sources) -> (status, findings)`
- PII: 手机号 `1[3-9]\d{9}`、邮箱、身份证号、内网 IP。
- 私有实体: `personal/`、`notes.json`、考试答卷特征(评分/答案)、能力评分(`estimated_level`/`confidence`)、记忆内容、简历项目细节关键词。
- 命中即 `FLAGGED`, findings 列 `{type, snippet, reason}`; 无命中 `CLEAN`。

### 5.2 事实检查 `fact_check(md) -> (status, findings)`
- 未证实量化声明: 「提升/降低/远超/领先/第一/唯一/业界」+ 百分比/倍数 组合 → `FLAGGED`, 需人工复核。
- 链接检查: 提取 `[t](url)`, 校验格式; 可达性用 `httpx` HEAD(失败仅记 warning, 不阻断)。
- 无声明且链接合规 → `PASSED`。

### 5.3 版权检查 `license_check(source_ids) -> (status, findings)`
- 每个引用 source `license_status == CLEARED` 或 `kind in (ORIGINAL, GENERATED)`。
- 任一 `UNKNOWN/BLOCKED` → `BLOCKED`。

### 5.4 Markdown 校验 `markdown_lint(md) -> findings`
- frontmatter 必填: `title`/`slug`/`category`/`level`。
- 代码块围栏闭合、标题层级、相对链接不以 `file:` 开头。

---

## 6. GitHub 发布服务 `server/app/services/github_publish.py`(新建)

**真实 GitHub REST**(`httpx`, 复用现依赖; 不装 PyGithub)。全部操作幂等、可 dry-run。

```python
class GitHubPublisher:
    def __init__(self, settings): ...          # token/api_url/repo/branch

    async def _req(self, method, path, **kw) -> dict: ...   # 统一鉴权/错误归一

    # 幂等: 同 idempotency_key 已存在运行 → 直接返回已有结果
    async def get_base_sha(self) -> str                    # GET /repos/{repo}/git/ref/heads/{branch}
    async def create_branch(self, name, base_sha) -> bool  # POST /repos/{repo}/git/refs
    async def write_file(self, path, content, message, branch, sha=None) -> str  # PUT /repos/{repo}/contents/{path}
    async def create_pr(self, title, head, base, body) -> dict  # POST /repos/{repo}/pulls
    async def get_workflow_runs(self, head_sha) -> list    # GET /repos/{repo}/actions/runs?head_sha=
    async def get_run_status(self, run_id) -> dict         # GET /repos/{repo}/actions/runs/{id}
    async def merge_pr(self, pr_number, method="merge")    # PUT /repos/{repo}/pulls/{n}/merge (仅 auto_merge)
    async def revert_file(self, path, base_branch, commit_msg) -> None  # 回滚: 写回上一版本
```

**dry-run 模式**: `publish(dry_run=True)` 只做完整校验 + 构建文件清单 + 生成即将执行的 git 操作序列(返回 `planned_operations`), **不触任何 GitHub 请求**。这是显式预览, 非 mock。

**真实 path**: `publish(dry_run=False)` 走真实 REST; 未配置 token/repo 时抛 `HTTPException(400, "publish not configured")`, 绝不静默降级。

**产物路径映射**(写内容仓库时对齐 portal 结构):
- `content/{category}s/{slug}.md`(guide→guides, learning-path→paths, hands-on-lab→labs …)
- 同步写 `search.json` 增量条目(可选, 后续由 Actions/build.mjs 重算)。

---

## 7. API 路由 `server/app/api/v1/public_content.py`(新建)

全部 `Depends(get_workspace)` + `Depends(get_session)`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/public-content/drafts` | 建草稿(校验 frontmatter) |
| GET | `/public-content/drafts` | 列表(按 workspace 过滤, 支持 status 筛选) |
| GET | `/public-content/drafts/{id}` | 详情(含 sources/audit 摘要) |
| PATCH | `/public-content/drafts/{id}` | 编辑 body/frontmatter/sources |
| POST | `/public-content/drafts/{id}/validate` | 跑 5.1–5.4, 写 status → `NEEDS_REVIEW` 或 `VALIDATION_FAILED`, 落 audit + trace |
| POST | `/public-content/drafts/{id}/approve` | 人工批准 → `APPROVED`, 写 `DecisionRecord` + audit |
| POST | `/public-content/drafts/{id}/reject` | 拒绝 → `REJECTED` + audit |
| POST | `/public-content/drafts/{id}/publish?dry_run=true/false` | 发布(建分支→写内容→开 PR), 幂等; 各 span 进 trace |
| GET | `/public-content/publications/{id}` | 发布状态(含 build_status, 惰性回查 Actions) |
| POST | `/public-content/publications/{id}/rollback` | 回滚(须有上一 PUBLISHED), revert PR |
| GET | `/public-content/rules` | 读本 workspace 规则 |
| PUT | `/public-content/rules` | 改规则(upsert) |
| POST | `/public-content/drafts/{id}/export` | 导出 Markdown/JSON(下载/序列化, 供本地预览) |

**红线(第 8 条)落地**: 唯一的公开内容入口是 `PublicContentDraft.markdown_body`; 无任何从 Exam/Memory/Profile/Answer 直接转公开的路径; `privacy_scan` 对 body 全文扫描私有实体; 未 `privacy=CLEAN` 且 `approval=APPROVED` 的草稿不可 publish。

**Trace(第 9 条)**: publish/rollback 全程 `start_trace(kind="content_publish")` → span(`validate`/`github.create_branch`/`github.write`/`github.create_pr`/`github.wait_build`) → `finish_trace`; 审批/回滚写 `DecisionRecord` 与 `Event`。

---

## 8. 修改 `server/app/main.py`

`import` 追加 `public_content`, `include_router` 循环加入 `public_content.router`。

---

## 9. 开发顺序(每步可独立验证)

1. config 配置项(加字段即热生效)。
2. 模型 + `__init__` 注册 → 重启, 确认 `create_all` 建 5 表。
3. `content_validation.py`(纯函数, 可单测)。
4. `github_publish.py`(dry-run 先测, 真实 path 连 token 后测)。
5. `public_content.py` 路由。
6. main.py 注册 + 重启 + `curl` 逐接口验证。
7. 端到端: 建草稿 → validate → approve → dry-run publish → 真实 publish → GET publication(追踪 build)→ rollback。

---

## 10. 待确认项(阻塞 §7 真实 publish 的端到端验证, 不阻塞开发)

1. **独立内容仓库地址**: `TARGET_REPO` 用哪个 `owner/repo`?(是否新建, 例如 `Lueyu/agent-interview-content`, 还是复用现有仓库下某子目录, 或旧站仓库已删内容需另建)
2. **GitHub PAT**: 是否已在手(需 `repo` + `workflow` 权限)? 提供方式 —— 建议不贴进对话, 写入 `server/.env`(已 gitignore) 由我读取; 若暂不提供, 我先完成开发 + dry-run 验证, 真实 publish 留待 token 到位后联测。
3. **自动 merge**: 是否允许低风险内容配置 `auto_merge=True`(默认关闭, 保持人工合并 PR)。