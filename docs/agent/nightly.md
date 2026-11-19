# CareerPilot 夜间交付

由一条 Codex Desktop 项目自动化按北京时间 00:30 启动。手动演练使用同一组项目命令。每轮最多交付一项，领取后有 30 分钟用于发现和交付。使用 Git 主检出目录作为自动化的工作目录。关联工作树中的脚本也打开主检出目录下的 `.agent-runs/nightly.sqlite3`，同一仓库一次只有一个会话可领取。独立 clone 有自己的账本，正式自动化请使用同一检出。

## 发现与选题

1. 执行 `python scripts/agent/nightly.py begin`。使用返回的 `run_dir`、`base`、`owner` 和 `deadline`。若 `proceed=false`，结束本次触发。当天已有终态时保留原记录。
2. 用 `project-discovery` 对照当前产品目标、近期用户反馈、失败记录、核心用户路径和最近改动。核对当前实现，查找进行中的工作与既有候选，按候选编号和实际行为去重。在 `discovery.md` 记录证据、优先级和取舍。
3. 从当前授权覆盖的候选中选择一项。自动化的授权范围来自宿主任务提示和用户明确给出的范围。有持续授权时，可以直接完成其中可复验的本地改进。交付包含隔离工作树、验收证据和本地预览。提交、推送、部署、外部写入、修改日程和使用个人数据或真实模型分别依明确授权。
4. 在 `report.md` 记录候选编号、目标、授权来源、精确文件、基线、验收条件和预览目标。定时运行另记 `scheduled_at` 和 `started_at`。前者取宿主提供的计划触发时间，缺失时填 `null`。后者记录实际领取时间，可从 `deadline` 减去 30 分钟得到。没有合适候选时执行 `python scripts/agent/nightly.py finish no_task --note "本轮判断依据"`。已选任务遇到路径冲突、缺少运行前置或超出预算时登记 `blocked`。

## 交付一项候选

1. 执行 `python scripts/agent/nightly.py prepare --approval "<候选编号及授权来源>" --paths <文件> [更多文件]`。对同一候选和同一授权沿用相同的 `--approval` 值。入口检查主检出的 HEAD 和选定文件，并从基线准备独立工作树。若依赖主检出未提交的内容，记录具体阻塞。只有领取会话可以登记改动和终态。
2. 在返回的 `task_workspace` 使用 `project-delivery`。复现问题，再修改获准文件。按锁文件准备依赖。数据库测试使用独立测试库，页面验收使用回环地址。
3. 执行 `python scripts/agent/nightly.py checkpoint`。将返回的基线、工作树和候选补丁交给验证入口。

   ```powershell
   pwsh -NoProfile -File scripts/agent/verify.ps1 -RepoRoot <task_workspace> -RunDir <run_dir> -ChangePatch <run_dir>/change.patch -BaseCommit <base>
   pwsh -NoProfile -File scripts/agent/preview.ps1 -RunDir <run_dir> -Check
   ```

4. 按任务的验收条件补跑相关检查。把结果和路径写进报告。界面任务在隔离应用实例中执行实际操作。候选变动后，重新 checkpoint、验收并生成预览。证据预览的临时 HTTP 地址会在 `-Check` 完成后关闭。
5. 按 `workflow-retro` 写下结果、待评价项和查证条件。验收全部通过后执行 `python scripts/agent/nightly.py finish passed --note "交付摘要"`。账本保存补丁摘要、固定验收尝试及预览引用。行为检查不通过时执行 `finish failed`，缺少依赖或时间时执行 `finish blocked`，并在 `--note` 写明原因。晨报引用本轮实际状态和证据。

## 继续运行或处理中断

`python scripts/agent/nightly.py status` 查询进行中的运行或最近一次终态。原会话在截止前再次 `begin` 可以继续工作。到期后入口拒绝准备工作树、更新补丁和登记通过，并保留领取记录。截止时间是脚本的交付边界。外部模型请求、前台验证和其他子进程的退出状态由宿主提供或单独核实。

到期后，由另一个会话检查宿主状态和已启动的子进程。已确认原会话和相关子进程结束时，执行以下命令并在说明里引用证据。宿主明确报告取消时使用 `--result cancelled`。状态未知时保留领取记录和现场。

```powershell
python scripts/agent/nightly.py recover --stopped-thread <原会话 UUID> --note "停止证据与待处理事项"
```

默认恢复终态为 `interrupted`。恢复保留补丁、日志和工作树。同一天保留此终态，次日按当前基线、授权和已有证据重新发现任务。定时运行的报告把 `finish` 或 `recover` 实际返回的终态 `phase` 记为 `outcome`。尚未登记终态时写 `null`，并记下当前阶段。
