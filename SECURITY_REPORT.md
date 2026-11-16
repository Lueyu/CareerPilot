# 安全报告

> 版本：v3.2 · 2026-11-16 · local-first 单机部署威胁模型
> 定位：CareerPilot 运行在用户本机（localhost），数据库不暴露公网；公开网站为纯静态 OSS。本报告按此威胁模型评估，不夸大也不隐瞒。

## 已实现的控制

### 鉴权与隔离
- API Token（Bearer 或 X-Api-Token，可配置 `API_TOKEN`），默认 `cp-dev-token` 仅限本机开发；`/healthz` 外全部业务路由需鉴权。
- 工作空间隔离：全部业务查询经 `get_workspace` 依赖过滤；跨 workspace 资源访问 404/403（私有题库归属校验 `_own_question`）。
- 删除类危险操作双确认：workspace 删除需输入 slug（422 守卫）；回滚发布需确认弹窗。

### 数据红线（个人数据不外泄）
- 公开内容发布管线内置四重校验（`content_validation.py`）：隐私扫描（邮箱/手机/身份证/私域标记词→FAIL 阻断）、事实核查（量化声明无引用→WARN 降级）、许可检查（RESTRICTED→阻断）、Markdown lint。
- 高风险内容（校验 FAIL）强制人工审核，即使 auto_merge 开启（`HIGH 强制 False 不可跳过`）。
- 私人数据（考试答卷/记忆/能力画像/简历）永不进入公开草稿：公开草稿仅由项目扫描/学习材料等显式来源生成，license_status=OWNED。
- 发布幂等键唯一约束防重复发布；dry-run 模式零真实写入。

### 密钥管理
- LLM key / GitHub token：页面级配置存本地 DB（app_settings），API 返回仅掩码（`****尾4`）；`.env` 不入库（.gitignore）；对话/日志不回显密钥。

### 依赖与供应链
- Python 12+2 依赖全部主流维护包；OCR 用本地 RapidOCR（无外部 API 调用）；前端 71 npm 包锁版本（package-lock.json）。

## 已知边界（诚实声明，详见 KNOWN_ISSUES）

1. 单用户本地部署：无完整用户体系（登录/会话/密码），API Token 是共享静态令牌——不能防御本机其他使用者，这是 local-first 的明确取舍而非遗漏。
2. CORS 允许 5173（前端 dev 端口）；生产如变更端口需同步修改。
3. `DEFAULT_WORKSPACE_SLUG` 未鉴权区分多用户——多工作空间是「多套数据」而非「多用户权限」。
4. GitHub token 权限取决于用户授予范围（repo 全量），建议使用 fine-grained token 限定 agent-interview 内容仓库。
5. 隐私扫描是规则匹配（正则/关键词），对语义级隐私（如「我女朋友叫…」）无识别能力；事实核查为「量化声明必须有引用来源」结构规则，不做事实真伪判断。

## 结论

在「单机本地 + 本机用户可信」威胁模型下，当前实现满足任务书全部安全要求：鉴权、工作空间隔离、数据红线、发布审批与审计、密钥不回显、幂等与 dry-run。
