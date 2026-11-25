# 参与 CareerPilot

你可以从一道题的纠错、一次界面试用或一个小补丁开始。使用问题和岗位建议可以在 Discussions 里聊。碰到能复现的问题，先搜一下已有 Issue，再选择反馈表单。想动手修，可以在对应 Issue 下留言说明打算改哪里。

[交流想法](https://github.com/Lueyu/CareerPilot/discussions) · [搜索已有 Issue](https://github.com/Lueyu/CareerPilot/issues) · [提交反馈](https://github.com/Lueyu/CareerPilot/issues/new/choose)

## 第一次参与

| 可以做什么 | 从哪里开始 | 提交什么 |
| --- | --- | --- |
| 纠正一道公开题，无需写代码 | 读仓库里的[异步导入题及评分要点](server/app/showcase_questions.py#L26)。从「如果两次请求的文件不同」这个追问开始。 | 在 **使用反馈** 中写下题目、你认为也应得分的回答，以及理由。有公开来源时附链接。 |
| 试用学习入口，无需写代码 | 在「林舟的求职笔记」打开 **考试与面经 → 查看题库**，选一道题，找到 **围绕这个问题学习**。 | 记录你在哪一步找不到入口、当时看到的按钮，以及你期待看到什么。截图只保留虚构记录。 |
| 改进一道题的呈现 | 检查题库里公式、代码块或长文本在窄屏下的显示。[题目呈现组件](web/src/components/BankPresentation.tsx) | 用公开题或虚构题复现，记录修改前后的浏览器和视口宽度。 |
| 补一条复测回归 | 从学习笔记的复习入口进入复测。[笔记页面](web/src/pages/StudyNotebook.tsx) · [后端复测测试](server/tests/test_reviewed_retest.py) | 用合成作答验证复测报告对原失分项的覆盖与结果，附定向测试输出摘要。 |

Issue 和 PR 都是公开内容。请用公开题、虚构数据或脱敏后的最小示例。发布前移除简历、个人作答和学习记录、账号信息、私有仓库内容、访问令牌、连接串和本机私有路径。只附脱敏截图或必要的错误片段。

## 在本地开发

使用 Python 3.12、Node.js 24 和 PostgreSQL 16。准备专用的本地开发数据库以及有权访问它的开发账号。后端测试脚本还需要该账号能连接 `postgres` 并创建测试数据库。以下命令使用 Windows PowerShell。

在一个终端从仓库根目录启动后端。把命令里的示例连接串换成开发库连接串。

```powershell
cd server
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r requirements-dev.txt
$env:DATABASE_URL = 'postgresql+asyncpg://dev_user:dev_password@127.0.0.1:5432/careerpilot_dev'
python bootstrap.py
python migrate.py
python -m app.seed
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

打开 `http://127.0.0.1:8000/healthz` 确认后端启动。默认评分可走本地规则引擎。需要外部模型的场景再单独配置模型。

在另一个终端从仓库根目录启动前端。Vite 会显示本机访问地址，通常是 `http://127.0.0.1:5173/`。

```powershell
cd web
npm.cmd ci
npm.cmd run dev -- --host 127.0.0.1
```

## 验证和提交改动

### 添加或修改双语文案

组件里的按钮、标签、占位符和状态提示使用 `useT()` 读取。按功能在 `web/src/locales/zh-CN/` 和 `web/src/locales/en/` 添加同名键，两种语言共用命名占位符，例如 `{count}`。机器使用的动作名、枚举值和请求字段名保持稳定。用户提交的简历、题目、笔记和引文按原文显示。

前端首次打开时按浏览器语言选择，手动切换会记在当前浏览器。工作区 API 通过 `Accept-Language` 接收选择。服务端的界面提示和学习生成规则由 `server/app/locales/` 加载。后台生成任务应在提交时保存目标语言，以便重试时沿用。公开知识门户在 `portal/locales/` 维护导航、搜索和页面说明。

### 本地检查

前端改动在 `web/` 运行相关检查。

```powershell
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
```

后端改动在 `server/` 使用上面配置的开发数据库连接运行隔离测试。可将 `tests` 换成相关测试文件，例如 `tests/test_reviewed_retest.py`。脚本每次创建并保留一份独立的 `*_test` 数据库。

```powershell
python run_isolated_tests.py tests -q --tb=short
```

界面改动请在浏览器重走受影响的操作。文档改动请检查链接、命令和页面名称。从上游仓库最新的 `dev` 建分支，完成后把 PR 的目标选为 `Lueyu/CareerPilot:dev`。用模板写清改动原因、影响范围、实际运行的检查以及未验证的场景。有相关 Issue 时附编号。

维护者审核最终提交后合入 `dev`。负责人 Lueyu 定期测试这个分支，通过后将验收的版本合入主干 `master`。[主干发布流程](docs/agent/release.md)
