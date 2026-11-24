# 睡前审核与夜间开发

北京时间 21:00，Codex 研究 CareerPilot 和 Squabble，目标在 22:00 给你第一批建议，留出睡前审核时间。22:45 开始执行已经采纳的任务。你可以在任意聊天回复「批准 CP01、SQ02，先做 SQ02」，也可以写「CP03 改成只做本地导出，按这个版本开工」。Codex 把原话登记为决定，逐项验证、审核并合入各项目的 `dev`。晨报提供两个 `dev` 的本地体验入口。CareerPilot 由负责人定期测试并按[主干发布流程](release.md)将已验收版本合入 `master`。Squabble 仍由你体验确认后合入 `main`。

两条自动化均从 CareerPilot 主检出调用 `scripts/agent/nightly.py`，共享本地账本 `.agent-runs/nightly.sqlite3`。从开发工作树或其他聊天处理回复时也要调用这个控制器。工作区和候选分支属于各自产品。历史单任务运行通过 `legacy` 查阅。

## 整理和审核一批建议

每个业务问题沿用稳定编号。新增问题使用全局未用过的编号，重新评估旧建议时保留原决定。按实际证据描述现状，区分可复现缺陷、代码观察、外部研究和需要验证的假设。和最新的 `origin/dev`、开放 PR 及在途修改对照。对跨两个项目的探索分别写清受益对象。

将同批建议写入本机忽略目录下的 JSON 文件。顶层有 `batch` 和 `tasks`。每项登记 `id`、`project`、`repo`、已验证的完整 `base` 提交、`title`、`scope`、`evidence`、`acceptance`、`depends_on` 和整数 `priority`（越大越先）。范围可以是文件或以斜线结尾的目录。建议单还可提供 `benefit`、`approach`、`difficulty`、`kind` 和源码指针。通过实现方案暂时无法验证的收益，写成可试验的假设。

```powershell
python scripts/agent/nightly.py propose --file $batchFile
python scripts/agent/review_board.py evening --file $batchFile --output $eveningReport
python scripts/agent/nightly.py status
```

将摘要和完整建议单直接发给用户，数量由有价值的机会决定。只有收到人的明确回复后才登记决定。以下为处理回复的命令示例，编号及理由取自当次用户消息。

```powershell
python scripts/agent/nightly.py decide --id $id --decision approve --reason $userReply --reply $userReply
python scripts/agent/nightly.py decide --id $id --decision defer --reason $userReply --reply $userReply
python scripts/agent/nightly.py decide --id $id --decision reject --reason $userReply --reply $userReply
python scripts/agent/nightly.py decide --id $id --decision revise --file $revisedTaskFile --reason $userReply --reply $userReply
```

采纳可同时指定 `--priority`。用户明确说按修改后的版本开工时，先登记新内容，再用同一条回复批准该版本。用户只要求修改建议时，修订后继续待审。未被采纳的前置项留待用户选定。若晚间自动化已完成空队列检查，用户随后要求开工的聊天可立即读取同一账本，不用等到次日。

## 实现获批项

1. 取 `status` 的 `ready`，按依赖和优先级安排。先从对应仓库执行 `git fetch origin dev`，再从控制器执行 `claim --dev --id ID --owner OWNER`。在返回的 `workspace` 中创建独立的 `codex/<任务>` 功能分支。需要前置提交的任务等该 PR 合入 `dev` 后开工。
2. 在独立数据库和回环端口验证。复现原问题或建立验收用例，修改后运行相关测试。涉及交互时在实际页面完成批准的操作。保留每次尝试的日志。任务受阻时记下原因并继续其他就绪项。
3. 每项开一个目标为 `dev` 的 GitHub PR。独立复核 PR 的确切 head、用户场景和必需检查，将测试、提交、限制与体验步骤写入运行目录的 `report.md`。通过后按该 head 合入 `dev`，再 fetch 并登记 GitHub 的合入回执。
4. 所有可交付项合入后，按[本地部署说明](candidate-preview.md)部署各项目刚读取的 `origin/dev` 提交。用独立数据库与回环端口验证组合后的主要操作，把两套应用留到早晨。
5. 执行中断时先查原会话、工作树和 GitHub PR。确认原执行者停止后用 `recover --id ID --owner OLD --new-owner NEW --confirm-stopped --reason TEXT` 接管现有分支。PR 已合入时直接补记回执。任务持续时间没有固定上限。

第一批已领取的本地任务由当前所有者在对应仓库抓取 `origin/dev` 后，再执行 `claim --dev --id ID --owner OWNER`。转换要求原领取基线已包含在 `origin/dev` 中，现有工作树、分支提交和领取基线会保留。转换后通过 `record-dev` 登记已合入的 PR。

```powershell
python scripts/agent/nightly.py record-dev --id $id --owner $owner --pr $prUrl --head $reviewedHead
```

## 早晨打开成果

开发者在队列告一段落或遇阻时生成统一晨报并发回本地链接。默认涵盖两个项目，可用 `--ids` 限定本晚处理过的编号（含往日获批项），或用 `--batch` 回看一批建议。

```powershell
python scripts/agent/review_board.py morning --output $morningReport --dev-preview $careerPilotRun --dev-preview $squabbleRun
```

晨报提供部署的 `dev` SHA、功能 PR、体验步骤、验收结果和待解决事项。通过验收且部署提交仍包含在对应仓库本地 `origin/dev` 中的预览会显示应用链接。验收失败的部署仍会显示证据入口。用户体验后决定是否把对应 `dev` 提交合入主干。

收到对已验证候选的明确反馈后，选用 `accept`（接纳）、`return`（要求修改）或 `decline`（放弃）。将用户原话登记到账本，晨报会显示对应决定。退回或放弃的改动先通过新的修订或回退 PR 处理，再交付包含该变更的 `dev` 批次。

```powershell
python scripts/agent/nightly.py feedback --id $id --decision $decision --reason $userReply
```

按部署手册停止已经完成体验的候选应用，保留反馈与证据。Noodle 可作隔离试验，常规任务直接由 Codex 执行。
