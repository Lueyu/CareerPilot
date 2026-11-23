# 在本地交付 CareerPilot 改进

用可追溯的证据改进求职学习过程。优先完成一条用户能走通的路径，例如从本次评分的失分项进入任务，再用同能力域的新题检查原失分项。

## 按已确认的产品决定推进

| 已确认的决定 | 当前依据 |
| --- | --- |
| 能力画像使用符合条件的独立作答及评分证据。证据不足时显示 `UNKNOWN`。同源题重练可更新表现，独立覆盖按题组计算。 | [能力估计](../../server/app/services/competency.py) |
| 学习完成记录进度并安排复测。复测采集新作答和评分，分别显示整题表现和原失分项的覆盖及通过情况。 | [学习闭环](../../server/app/services/learning_loop.py)、[正式复测回归](../../server/tests/test_reviewed_retest.py) |
| 面经、项目扫描和生成草稿供学习与审核。题目通过审核后才进入题库。项目题引用来源区分 `user_statement` 和 `repository_observation`，缺少本人经历证据时保留 `needs_context`。 | [补题工作台指南](../ai-reports/04-core-loop/README.md)、[项目题生成及校验](../../server/app/services/question_generation.py) |
| 默认进入 `personal`，Demo 显式进入。公开写入和 Evolution 策略激活在当前本地模式关闭。 | [运行配置](../../server/app/config.py)、[个人使用说明](../../QUICK_START_PERSONAL.md) |

岗位标准与评分规则仍需校准。学习效果、延迟保持、真实供应商调用和某项改动的收益都要按各自证据判断。把待证实的判断写成假设，附可以证实或推翻它的观察方法。旧报告可定位场景和证据，当前行为以工作区对应版本的源码与本次实测为准。

## 运行三个项目技能

| 方法 | 输入与结果 |
| --- | --- |
| [项目发现](../../.agents/skills/project-discovery/SKILL.md) | 从项目目标和实际入口查证问题。给出有来源的候选、预期的用户可见变化和验收条件。 |
| [项目交付](../../.agents/skills/project-delivery/SKILL.md) | 在获准范围内完成一项改动。提供本次验证结果、可打开的本地预览或适用的其他证据，以及回退定位。 |
| [工作流复盘](../../.agents/skills/workflow-retro/SKILL.md) | 从原始发现、交付记录和用户反馈提取可验证的改进建议，保留验证与启用所需的依据。 |

将当前产品目标、用户反馈、运行记录和源码作为选题依据。用同一候选编号串起发现、交付和反馈。再次考虑同一问题时，查证它在当前版本仍存在，并说明此前尝试后的变化。

## 运行验收并打开预览

为每次运行建立独立的 `.agent-runs/<run>/` 目录。从待验收版本的仓库根目录用 PowerShell 7 把该目录传给验证入口。读取同一目录下的 `verify.json`，再按 `attempt` 找到本次的 `inputs.json` 和检查日志。[验证入口](../../scripts/agent/verify.ps1)

```text
scripts/agent/verify.ps1 -RunDir <dir> → <dir>/verify.json
```

从原工作区调用脚本来验收另一份工作树时，用 `-RepoRoot` 指向该工作树。将回执中的 `workspace` 与待验收根目录核对。[验收与预览命令](../../scripts/agent/preview.md)

按改动补足用户路径验证。后端数据库测试从 `server/` 用项目 Python 执行 `python run_isolated_tests.py tests -q --tb=short`，也可传入相关测试文件。脚本为每次调用创建新的 `*_test` 数据库。前端从 `web/` 使用 `npm.cmd test`、`npm.cmd run typecheck`、`npm.cmd run build`。按验证入口实际执行的范围记录覆盖，对未覆盖的相关场景单独补证。[隔离测试脚本](../../server/run_isolated_tests.py) · [前端命令](../../web/package.json)

涉及页面或端到端行为时，用本次候选版本启动隔离实例。确认 API 与页面指向同一验收实例，并在浏览器走到相关页面执行实际操作。记录回环地址、路由、操作、可见结果，以及对应的接口或持久化证据。健康检查用于确认服务已就绪。涉及学习闭环时，核对本次原答、评分项、任务和新题复测记录能按 ID 串起来。需要真实模型时按本次授权和可用配置运行，并标明真实模型、受控夹具和未验证项。[应用路由](../../web/src/App.tsx) · [浏览器验收实现](../../server/acceptance/study_browser.py)

用 `scripts/agent/preview.ps1 -RunDir <dir>` 打开绑定在回环地址的验收证据页。预览读取运行目录的 `verify.json` 指向的验收尝试，生成 `<dir>/<attempt>/preview-*/preview.html` 和同目录的 `preview.json`。从 `preview.json` 取实际地址，核对其中的 `attempt`、`verify_status` 和页面摘要。使用 `-Check` 时脚本完成 HTTP 检查后退出，报告引用保存的 HTML 并标明临时地址已关闭。浏览器中的产品行为仍由隔离应用实例验收。[证据预览入口](../../scripts/agent/preview.ps1) · [预览实现](../../scripts/agent/preview.mjs)

个人使用入口 `deploy/start-personal.ps1` 会运行数据库初始化和迁移，并写入个人运行目录。隔离验收使用自己的测试库、端口和进程，避免影响个人入口。临时应用实例退出后保留截图、日志或浏览器报告，并标明地址已失效。[个人启动脚本](../../deploy/start-personal.ps1)

## 保持每次运行可核对

Codex 原生 Memories 用于召回以往的偏好和尝试。项目已确认的决定记录在本文并指向依据，强制协作规则记录在 `AGENTS.md`。核对本机开关使用 `codex features list` 的 `memories` 行。白天交付在运行开始时执行 `python scripts/agent/nightly.py versions --run-dir <run_dir>`，夜间入口自动执行同一记录步骤。`versions.json` 保存项目技能内容的 SHA-256 和当前会话可读的模型配置。模型配置缺失时留空。

在运行目录保存发现、交付、复盘报告及引用的验证结果。报告记录目标、授权范围、基线、候选版本、实际执行命令与结果。未提交版本记录 Git HEAD，并列出本次验证所用的改动文件和相关未跟踪文件及其摘要。仅引用 Git HEAD 时，范围限于该提交收录的内容。

核对 `inputs.json` 中的路径和摘要与实测版本一致。源文件相对待验收版本的仓库根目录解析，`verify.json` 中的检查证据和输入清单相对该回执指向的尝试目录解析。打开报告引用的本地产物，确认内容属于本次运行。区分检查失败、无法运行和未覆盖。保留能定位本次改动并安全撤销的信息，避免覆盖工作区里的其他编辑。报告只收录脱敏的业务证据和日志。

## 睡前选题与晨间验收

每天北京时间 21:00 开始研究 CareerPilot 和 Squabble，目标 22:00 交付可审核的建议。你在 Codex 聊天直接回复编号即可采纳、修改、调优先级或暂缓。22:45 的开发任务从同一账本读取明确获批的工作；你也可以要求收到批准后立即开工。每个项目沿用自己的验收入口。[操作手册](nightly.md)

夜间按前置关系领取任务，独立工作使用各自的工作树、数据库和端口。每项先独立发 PR，验证通过后合入 `dev`，本地部署组合后的 `dev`。早上打开晨报里的应用链接，按体验步骤验收，再决定是否统一合入主干。每项都保留 PR、基线、通过的测试及未覆盖的边界。[本地应用预览](candidate-preview.md)

## 用真实交付样本评估项目技能

本机 SkillOpt-Sleep 试点只读取项目自有的 `.agents/skills/project-delivery/SKILL.md`，关闭记忆文档优化和自动采纳。原始采集计数、mock 验证和 Windows 兼容性日志在 `.agent-runs/skillopt-pilot/`。获得匹配本项目的真实交付轨迹后，以独立任务复验候选技能，并将回放评分与真实项目验收分别保存。Hindsight 的引入条件是能在实际任务中复现且衡量的召回问题。
