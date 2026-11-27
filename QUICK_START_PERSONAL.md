# CareerPilot 个人使用说明

打开 [本地个人应用](http://127.0.0.1:5173)。先确认当前工作空间为“个人学习空间 / personal”。第一次进入时，零证据、零学习任务和 `UNKNOWN` 是正常起点；Demo 可供显式切换查看，不能作为你的评估结果。

本机本轮最终交付启动状态：**已迁移并启动：8000 / 5173；personal 为 0 答卷、0 证据、0 任务、整体 UNKNOWN；原库全部既有行保留**。当前可直接打开页面；以后需要启动时，按下面的命令运行。

## 启动本地应用

### 安全首次安装与升级

个人 API、Worker、建表和迁移入口在连接数据库及恢复任务之前检查凭据。`API_TOKEN` 至少32字符，数据库密码至少24字符，空值、旧默认值、示例占位符、明显重复/顺序串会拒绝；`DEBUG` 不绕过检查。这只是明显弱值筛查，不能估计人为口令的真实熵。新凭据由 Python `secrets` 随机生成。

全新安装先在 `server/` 使用已安装依赖的 Python 创建运行配置：

```powershell
python personal_credentials.py init --output ../personal/runtime/personal.env
```

该命令只生成文件，不连接数据库、不创建或修改PG角色。文件含随机 API_TOKEN、POSTGRES_PASSWORD、DATABASE_URL 和 G5 主密钥；重复运行只校验，已有文件不覆盖、不换令牌。Windows 在写秘密前为新目录/文件设置并回读仅当前SID的受保护DACL，Linux使用0700/0600；权限无法确认则停止。已有文件和父目录权限不会自动修改。文件、主密钥及其私有备份不能提交、分享或放进前端产物。

新Compose卷可从仓库根使用该文件（生成URL供原生默认连接，Compose明确改用postgres主机）：

```powershell
docker compose --env-file personal/runtime/personal.env up -d --build postgres
docker compose --env-file personal/runtime/personal.env run --rm --no-deps server sh -c 'python personal_credentials.py check && python bootstrap.py && python migrate.py && python -m app.seed'
# 仅操作者核验的全新库/已知正常安装可显式登记；恢复库禁止此操作。
docker compose --env-file personal/runtime/personal.env run --rm --no-deps server python provision_recovery_control.py --confirm 'PROVISION KNOWN INSTALLATION careerpilot'
docker compose --env-file personal/runtime/personal.env up -d --build
```

Compose要求显式密码，不再提供通用默认值。上述流程只用于全新卷；已有PG卷的密码不会随环境文件改变，不能对旧卷直接套用新随机密码。需要原生PG时，由操作者先建立专用角色/数据库并在私有文件配置实际强密码，可用init的 `--host --port --user --database` 指定新安装连接目标。本脚本不执行ALTER ROLE，不修复旧密码。

已有安装先由操作者在维护窗口确认数据库及凭据；旧弱凭据会拒绝，需要人工完成安全迁移，不能靠debug或测试标志绕过。`deploy/start-personal.ps1 -EnvFile <绝对私有文件路径>` 可选择配置；未指定时优先使用已有 `server/.env`，其次使用生成文件；两者都不存在且没有外部凭据时才创建生成文件。进程环境变量优先，必须确认其目标。初始化不会改写已有文件。

F6仍要求显式登记已知安装：在 `server/` 配置同一个受保护环境并执行建表/迁移后，运行 `python provision_recovery_control.py --confirm 'PROVISION KNOWN INSTALLATION <实际数据库名>'`。恢复、未知来源或未登记数据库保持隔离；普通启动不插入NORMAL，也不恢复后台任务。只有确认来源的维护命令有登记权。

网页打开现有WorkspaceLogin，输入空间slug（通常personal）和私有文件的API_TOKEN。令牌仅由浏览器运行时保存在当前标签页sessionStorage；退出清除当前登录，重新登录重新绑定空间。切换菜单只影响下一次导航，旧页面请求保持原空间。`VITE_API_TOKEN`不再读取或注入，构建文件不含部署凭据。H1将继续处理token表、哈希、撤销/轮换、租户归属和恢复generation，本项不声称这些能力已实现。

要在播客页保存离线歌单，用 `./deploy/start-personal.ps1 -BuiltWeb` 启动。它把带有本机连接配置的前端构建保存在 `personal/runtime`，供这台电脑的回环地址使用。通过手机收听时，可以导出 MP3 传到手机。如果手机已有自己的受信任 HTTPS 工作区入口，可在手机上打开节目库，先保存歌单，再通过离线播放入口收听。

从题库导入时，可选「中文」「英文」或「中英双语」。双语节目先用中文讲清业务场景，再给一段可跟读的英文面试回答。同一道题可以分别制作三个版本。已有中文草稿在确认合成时使用当前安装的音色。

要使用本机神经语音，先安装 `server/requirements.txt` 的依赖，再从仓库根目录运行 `python deploy/install-podcast-voice.py`。安装器下载约 147 MB 的 Kokoro 中英文模型，核对 SHA-256 后保存在 `personal/models`。配音在本机 CPU 上完成。已有模型包可用 `--archive` 安装；自定义目录需同时设置 `CAREERPILOT_PODCAST_MODEL_DIR` 指向解压后的模型目录。

未安装模型时使用 Windows 的系统语音或 Linux 的 eSpeak NG。双语节目需要普通话与英语语音。离线收听和导出 MP3 的方式相同。[模型说明](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh)

已单独完成数据库迁移的现有实例可以加 `-DatabaseReady`，直接启动服务。新安装请使用默认的初始化流程。

### 查看一份完整的求职记录

在已装好本地依赖的仓库根目录运行 `./deploy/start-personal.ps1 -WithShowcase`。首次运行会新建「林舟的求职笔记」。这是为展示准备流程编写的虚构履历，包含私有题库、作答、复测、项目、学习对话和笔记。已有个人空间使用原来的资料。重复运行会保留你在新空间内做过的修改。

首次访问的浏览器会打开林舟的工作空间。已选过工作空间的浏览器记住原选择，点击左上角工作空间名称即可切换。如果服务已经在运行，可以在配置好当前本地数据库的 `server` 目录单独运行 `python -m app.showcase`，再从工作空间菜单进入。

需要本机 PostgreSQL 已运行，后端 Python 已安装 `server/requirements.txt`，前端已有 Node.js 与 `web/node_modules`。现有数据库及模型配置保留在本机，不需要复制进本说明。

```powershell
Set-Location -LiteralPath 'D:\agent-interview'
.\deploy\start-personal.ps1
```

脚本依次执行建表引导、兼容迁移、个人空间和共享数据初始化，然后在隐藏后台进程中启动 API 与页面。默认地址为 [页面 5173](http://127.0.0.1:5173) 与 [API 健康检查 8000](http://127.0.0.1:8000/healthz)，仅绑定 loopback。初始化不会编辑 `server/.env`，也不会为 personal 生成示例证据或示例任务。

脚本优先使用依赖完整的项目虚拟环境，再尝试系统 Python；本机原 `.venv` 曾缺少运行依赖，不能仅凭目录存在判断可用。指定解释器或更换端口时可用：

```powershell
.\deploy\start-personal.ps1 -PythonPath 'C:\path\to\python.exe'
.\deploy\start-personal.ps1 -Port 8001 -WebPort 5174
```

其他参数：`-InitializeOnly` 只初始化，`-NoWeb` 只启动 API。端口已被占用时脚本会停止并提示，不会替你杀掉现有服务。日志及本次进程编号在 `personal/runtime/` 的 `api.out.log`、`api.err.log`、`web.out.log`、`web.err.log` 和 `processes.json`。

如果当前 PowerShell 曾为验收设置临时 `DATABASE_URL`，启动前应确认它指向预期库；进程环境变量会覆盖 `server/.env`。不要把验收库误当成正式个人库，也不要把原库作为自动化测试目标。

## 完成一次真实学习闭环

1. **检查空间和模型。** 在“设置与工作空间”确认 personal；切换列表来自真实数据库。开放题的非空回答需要可用模型，在模型设置中确认连接。首次没有证据时不需要手动制造画像或任务。
2. **开始诊断并作答。** 从考试中心创建考试，输入自己的答案。保存失败时页面会保留输入并显示错误；先重试保存，不能把尚未保存的内容当作已提交。等待本次评分，不要用旧报告替代本次结果。
3. **阅读本次报告。** 确认考试 ID、题目、原答、分数和缺失评分项。报告中的低分描述本题表现；整体 `UNKNOWN` 表示独立有效证据仍不足。评分服务失败时没有有效总分，可在同一报告重试评分。
4. **打开报告关联的任务。** 进入具体失分项的学习任务，查看原题、原答摘录、原因和来源证据，再读取教学内容、记录自己的理解或实践产物。任务不是从空白画像凭空生成的待办。
5. **记录学习完成。** 完成只改变学习进度，可保存学习产物，并从完成日起安排 1、3、7 天复测；不会直接加分，也不会使复测自动通过。
6. **即时回答一道新题。** 从已完成任务点击“即时新题验证”，独立作答并提交。新题应属于同一能力域且不重复原 canonical 题组。没有可用新题会明确提示，需要先补充适合的题目。
7. **核对新的结果。** 查看新考试报告及复测记录，分别看本题整体结果、原失分评分项是否覆盖、该评分项是否通过。新题没有覆盖原项时，原项仍是未验证；即时检查也不代表已经保持 1、3 或 7 天。
8. **到期再做延迟复测。** 未到期的计划复测会被后端拒绝。过期或放弃的答题记录保留，可从相应任务重开；评分服务失败则在原考试重评。

历史报告、证据和任务保存于 PostgreSQL。页面刷新或重启后应按原 ID 继续读取。验收用的示例答案与评分不代表你的能力，正式 personal 不会自动复制这些验收记录。

## 备份与安全恢复

使用[加密备份与隔离恢复操作手册](deploy/BACKUP_RECOVERY.md)。备份输出为完整 age bundle 目录，公钥、解密 identity 和 G5 主密钥分别在归档以外保存。恢复必须指定新目标、私有回执目录、Python 验证器并确认目标未路由；受控备用库恢复还需绑定计划的确认和当前库新安全备份。所有恢复保留隔离，旧 token 和旧预算不会自动启用。

升级前在维护窗口对已知安装执行迁移及 `provision_recovery_control.py` 的显式确认。未知或恢复数据库禁止登记 NORMAL，普通启动不会自动补状态。个人数据、原答和能力证据不做重写。

## 当前边界

- 当前岗位标准和不少评分项仍来自种子数据，粒度较粗。模型分数、三道独立题目的估计门槛及置信度规则均未校准，不等于招聘结论。
- 同题重练记录最新表现，但不会当作新的独立覆盖。学习完成、总题得分和原失分项通过是不同记录。
- 公开发布、远程写入、PR、合并和远程回滚已在后端关闭；本地草稿和 dry-run 预览可用。已有 GitHub token 不会打开这些能力。
- Evolution 可以保留候选和评估记录，本轮禁止激活或应用策略。没有经过真实时间的延迟复测，不能声称长期记忆保持。
- 本地验收命令及证据格式见[交付工作流](docs/agent/workflow.md)。
