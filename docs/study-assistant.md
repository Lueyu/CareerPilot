# 在 CareerPilot 中学习和复测

在侧栏打开「学习聊天与笔记」，输入问题并开始学习。题库的题目、已评阅试卷的逐题反馈以及学习计划中的任务都有「围绕这个问题学习」入口。进入后可以先看带入的原题、原答、评价或学习目标。历史会话可改标题、结束并继续。支持语音转文字的浏览器会出现听写按钮。

| 想做什么 | 在哪里做 | 数据如何保存 |
| --- | --- | --- |
| 理解概念、机制与设计缘由 | 直接追问，或点「讲实现机制」「讲设计缘由」「结合我的项目」 | 先保存提问和生成占位，再分段保存回复。中断的回复保留部分内容和状态。未读到的项目资料要标记缺口 |
| 把对话留作笔记 | 「整理成笔记」或「结束并整理」 | 后台任务冻结提交时的已完成消息。新消息通过下次整理纳入。笔记正文和出处分开，原文引用可返回具体消息 |
| 更新已经手写过的笔记 | 「我的笔记 / 复习」里打开、编辑、保存 | 手工保存生成新版本。晚到的整理结果、基于手工版本生成的更新可从历史里检查后采用。另一会话可通过「关联已有笔记」指定同一笔记 |
| 准备复习 | 从指定笔记版本生成 2–3 张候选卡，编辑后启用 | 题目、答案、知识点绑定原版本。答案默认收起。自评“再来一次”“较难”“记住了”分别安排 10 分钟、1 天、3 天后出现，时间以服务端为准 |
| 试着独立解释 | 聊天内「让我试着解释」「测一下」，或从复习卡进入 | 每次一道题。原答与后续修正是不同记录。提示、参考答案、反馈曝光以及自评各自留痕，可跳过或继续讲解 |
| 核对能不能迁移 | 提交原答后点「生成新情境复测」，打开正式考试、提交 | 复用考试的冻结题目、评分运行和画像更新。缺合适的新题时明确提示。返回学习会话查看该复测的成绩、证据数和知识点状态 |

聊天、整理、卡片、自评和辅助练习均不产生独立能力证据。正式评分返回超时、格式无效或必要字段不全时，查看失败状态并重试原评分任务。只有合格的独立作答走现有业务入口。一次通过可以增加有效证据覆盖，等级是否可估由既有规则决定。学习计划原有的失分项复测仍按出题时冻结的审核映射判断，缺映射保留待验证。

## Windows 本地运行

需要 PostgreSQL、项目现有 Python 虚拟环境与前端依赖。在 `server/.env` 或已有应用设置中配置可用的模型提供商、地址、模型 ID 和密钥。配置空缺、网络错误和输出无效会显示失败状态。全新环境先按仓库 README 完成初始化和题库导入。已有个人库升级前先用 PostgreSQL 的备份工具保留可恢复副本，核对 `DATABASE_URL` 指向目标库。以下命令分别在对应目录运行。

```powershell
# server，已有库先应用迁移，然后分别在两个终端运行 API、worker
.\.venv\Scripts\python.exe migrate.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m app.project_worker
```

```powershell
# web
npm install
npm run dev
```

打开 [本地学习聊天](http://localhost:5173/study/chat)。新表由迁移 010–012 和模型注册提供。整理任务依赖 worker。服务重启后，已落库的消息、版本、卡片和正式评分运行都可重新读取。正在生成的聊天可立即中断或在工作租约到期后重试；笔记任务租约为 120 秒。

## 访问边界

个人本地模式沿用现有 API token。共享部署时，为每个已有工作空间分配单独的随机令牌，在服务端环境变量 `WORKSPACE_TOKEN_HASHES` 中保存 JSON，键为工作空间 slug，值为对应令牌的 SHA-256 小写十六进制摘要。用强随机值配置管理员 `API_TOKEN`。启用绑定时会拒绝内置开发管理员令牌。令牌绑定的工作空间是服务端访问边界。

共享部署的前端使用 `VITE_AUTH_MODE=workspace`，构建时移除 `VITE_API_TOKEN`，重启开发服务或重新构建。每个人在登录页输入自己的工作空间和令牌，凭据保存在当前标签页会话内。共享网络上请使用 HTTPS。后台 worker 使用相同数据库。

## 教学规则的来源

服务端教学规则版本为 `careerpilot-teaching-v2`。不同操作加载各自需要的提示。项目检查支持授权目录的真实源码、固定版本的分析快照与具体片段。当前界面、模型分档及运行方式见[学习教练说明](study-coach.md)。用户陈述、助手推断和可回到材料的事实要分别表达。

| 实际读取的来源 | 版本 / 许可证 | 本地适配 |
| --- | --- | --- |
| pstack teach、how、why | 安装包 0.9.139，MIT | 先直接解释；机制按输入、执行、结果追踪；设计缘由说明约束、备选与待核实动机。[上游技能目录](https://github.com/michael-denyer/pstack-claude/tree/main/plugins/pstack/skills) |
| Jane Street socratic-tutor | 469868f，MIT | 用户主动练习时每次只问一道，等待其原答再反馈。评价沿用产品评分链路。[固定版本](https://github.com/janestreet/agent_skills/blob/469868f4489cbdbe178c775a0af05bb6bef63689/socratic-tutor/SKILL.md) |
| Feynman Technique | fc4586e，MIT | 复述用自己的话，指出具体缺口，记录更正和类比边界。复习单不会认证掌握。[固定版本](https://github.com/evanshlee/feynman-technique/blob/fc4586ea68883068f694d70c8618ff9a2d1874a3/SKILL.md) |

这三组来源用本产品自己的中文规则实现。终端命令、写文件和多代理操作均由 App 的消息、资料与持久化服务替代。使用者只需打开 CareerPilot。

## 复跑验收

在 server 目录运行 `.\.venv\Scripts\python.exe run_isolated_tests.py tests/test_study_chat.py tests/test_study_notes.py tests/test_study_practice.py tests/test_study_ownership.py -q`。该命令创建新的 PostgreSQL 测试库。前端在 web 目录运行 `npm run typecheck`、`npm test` 和 `npm run build`。

真实浏览器验收在 server 目录运行 `.\.venv\Scripts\python.exe acceptance/study_browser.py`。需要可创建测试库的 PostgreSQL 连接、可计费模型和带 Playwright 的 Edge。可用 `STUDY_PLAYWRIGHT` 指定本机 Playwright 模块路径。脚本启动临时端口 18860/18861，保留独立的 `*_test` 库，将脱敏结果和截图写到 `.tmp/study-acceptance/<测试库名>/`。部署时可以在平台的实际网关控制台另行核对模型账单。

本机若反复出现 Python 原生异常窗口，可用同一个验收命令追加 `--windows-no-wmi`，只对该次验收进程和子进程启用 Python 的 Windows 平台信息回退。构建中遇到 esbuild 临时文件拒绝访问时，可将当前终端的 `TEMP` 与 `TMP` 指向项目内一个可写的临时目录再运行原命令。
