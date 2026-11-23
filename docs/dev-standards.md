# CareerPilot 开发规范（dev standards）

> 2026-11-23 与 lint 门禁一起落地（阶段 1+2）。目标：防止坏味道、统一风格、**不改变业务语义**。
> 引用基于 master `79b91c2`；业务语义红线见 `docs/design-patterns-map.md` 与 careerpilot-maintainer skill。

## 1. 分层

- 路由层 `server/app/api/v1/*`：只做 HTTP 事务——解析请求、鉴权、调用服务、异常→HTTPException、返回 schema。不写业务规则。
- 服务层 `server/app/services/*`：业务规则与编排；跨服务用函数级组合，不互相 import 路由。
- 数据层 `server/app/models` + SQLAlchemy async session（每请求一 session）；迁移走 `server/migrate.py`。
- 现状自省（2026-11-23 lint 门禁提交后）：`bank.py`(1331 行)/`exams.py`(1146 行) 路由层过厚 → 阶段 3 重构候选；**新代码不许再加厚路由层**。

## 2. 命名

- 服务模块按域前缀：`study_` / `learning_` / `assessment_` / `project_`；路由按资源名。
- 测试：`server/tests/test_<module>.py`；`web/tests/*.test.mjs`。
- 模块级常量全大写；私有函数 `_` 前缀；禁止单字符名（`l`/`O`/`I`）。

## 3. 错误处理

- 服务层抛**类型化异常**：`StreamFailure`（带 kind）、`StageError`、`PermissionError`/`ValueError`。
- 路由层统一翻译为 HTTPException + 正确状态码；禁止裸 `except: pass` 吞异常。
- 用户可读失败信息须含可重试语义（例："模型未完成回复（timeout），可重试"）。

## 4. 异步与并发

- async 路径内禁止阻塞调用（requests、time.sleep、同步 DB 驱动）。
- 长任务一律 Worker + 检查点（见 design-patterns-map #5/#6），API 进程不扛长任务。
- 超时预算模式：deadline 一次计算 + 每段检查 remaining（已知升级窗口问题待修）。

## 5. 配置

- 一切配置走 `server/app/config.py` 的 `Settings` 单源（`get_settings()` 缓存）；禁止散落 `os.getenv`。
- 关闭的特性不因环境变量意外打开（`PUBLIC_WRITES_ENABLED` / `EVOLUTION_ACTIVATION_ENABLED` 有测试守卫）。

## 6. 测试

- server：`server/.venv/Scripts/python.exe run_isolated_tests.py tests -q`（独立库 `careerpilot_suite_*_test`，禁止连个人库）。
- web：`npm test`（node:test）+ `npm run typecheck` 双闸，build 前必过。
- 红线：测试不得创建跨工作空间全局数据（2026-11-23 `/bank` 泄漏案例，见 design-patterns-map #11）；模型替身用 calls/outcomes 注入，不真调网关。
- 安全测试守卫：restore.ps1 拒绝个人库（test_local_safety）；测试产物不得写 `personal/`。

## 7. Lint 门禁（本次新增）

| 范围 | 工具 | 命令 |
|---|---|---|
| server | ruff（配置在 `server/pyproject.toml [tool.ruff]`） | `ruff check app tests` |
| web | eslint 9 flat config + prettier | `npm run lint` / `npm run format:check` |
| web 死代码 | knip | `npm run knip`（报告制，人工确认后删） |
| 重复代码 | jscpd | `npx jscpd --min-tokens 50 src server/app`（报告制） |

- 提交前：两条 lint + 双测试闸全绿；ruff `--fix` 只允许确定性行为保持修复（unused import/import 排序/pyupgrade），语义性重构走正常评审。
- `ruff format` 全库重排是可选大动作：执行时单独提交并配 `.git-blame-ignore-revs`，不与其他改动混编。

### 7.1 落地基线（2026-11-23 实测，零逻辑变更）

| 检查 | 基线 | 终态 |
|---|---|---|
| eslint | 50 problems（18 errors / 32 warnings） | **0 errors** / 32 warnings（全为 react-hooks/exhaustive-deps 与 ref-in-cleanup——有意不修：补依赖会改 effect 触发行为） |
| ruff | 1004 条（E702 423 / E701 262 / I001 209 / F401 57 / B904 13 / B905 11 / E402 8 / E731 6 / F841 5 / 其他 15） | **All checks passed**：255 safe-fix + 19 手工修复 + 2 处 noqa 回补 + 配置豁免；全量套件复跑验证 |
| prettier | 87 文件未格式化 | report-only：整库重排会破坏文档行号引用与 blame，独立立项 |
| knip | 17 个未用导出类型 + 若干未用导出函数 | report-only：删除零风险但 churn 大，留 backlog |

- **配置豁免（`server/pyproject.toml`，逐条带理由）**：`E701`/`E702`＝代码库刻意的单行紧凑风格（685 处，重排=巨大 churn）；`B905`＝zip 加 `strict=` 改变语义（长度不等抛错）需逐点决策；`B904`＝`raise ... from` 改变异常链可见性，留专门一轮。per-file-ignores：`app/models/__init__.py` F401（模型转发导出层）、`tests/test_engines.py` E402（分节测试文件）。
- **noqa 清单（8 处，全部带理由注释）**：pytest fixture 经模块命名空间注册的导入 ×3（`tests/learning_process_support.py` ready、`tests/test_learning_bank_boundaries.py` item、`tests/test_study_coach.py` 流式 fixture 组）、fixture 参数遮蔽导入 F811 ×1、B023 ×2（`study_chat.py` partial——已人工复核：fragments 每迭代重建且 partial 在同一次 create_task 内消费完，非 bug）、F401 ×1（`app/api/v1/study_chat.py` `project_sources`——路由不直接调用，但测试通过 `api.project_sources` 取证）、B007 ×1（`app/api/v1/career_assets.py` `for project, report`——循环变量在 break 后供 ：200-203 使用，for/else 选行模式）。
- **静态分析盲区实录（ruff --fix 曾引入 2 处回归，已由全量套件捕获并回补）**：①F401 看不见"模块命名空间契约"——`from app.api.v1 import study_chat as api` 后测试直接 `api.project_sources(...)`，模块内未用的导入可能是测试的 patch/调用目标；②B007 看不见"循环变量泄漏"——`for x, y in ...: if ...: break` 后在循环外引用 `x` 是合法且常见的选行模式。**推论：任何批量导入删除/变量改名必须以全量测试套件为提交闸门，静态分析绿灯不等于行为不变。**
- **F841 修复原则**：RHS 无副作用的整行删除；RHS 有副作用/起守卫作用的去掉赋值保留调用（如 `bank.py` scalar_one() 存在性守卫、seed/测试中的写库调用）。
- **已知遗留（记录不改，防顺手修坏语义）**：B904 13 处、B905 11 处、test_question_workshop PUBLIC 题跨测试泄漏（design-patterns-map #11）、E701/E702 整库重排。

## 8. Git 与文档

- 提交信息 `type: 中文主题`（type ∈ chore/fix/feat/docs/refactor）。
- 常规流程 PR → `dev`，验证后合入 `master`（见 `docs/agent/workflow.md`）；直推 master 须门禁全绿且属于用户已授权范围。
- `personal/` 永不入库（已 gitignore）；根目录不放 AI 过程报告——提炼进 `docs/ai-reports/`（政策见其 README）。
- 保留文档白名单见 `docs/ai-reports/README.md`「保留与移除说明」。

## 9. 安全红线（业务语义，不可顺手改）

1. 材料不等于能力证据；UNKNOWN 不是 0。
2. 学习完成只记进度；复测必须新作答、新评分。
3. 共享题只读；私有题/项目/来源做 workspace 校验。
4. LLM 输出是"输入"不是"输出"：流式边界校验（llm_stream），HTML 渲染前转义。
5. 无 mock 进生产路径；Demo 需显式进入，默认 `personal`。
6. 公开远程写入与 Evolution 激活在本地模式保持关闭。
