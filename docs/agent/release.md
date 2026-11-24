# 将验收过的 dev 发布到 master

功能开发与正式发布分两次审核。贡献者从上游最新的 `dev` 拉分支，改动通过测试后向 `dev` 开 PR。维护者独立审核最终提交，处理意见，复验改动，再合入 `dev`。日常交付给出合入后的完整 SHA、测试结果和可体验的入口。

## 负责人验收并发布

1. 负责人 Lueyu 按自己的节奏测试集成后的 `dev`。从实际运行的版本或部署回执记录完整 SHA 和测试结果。发现问题时，修复仍通过功能 PR 合入 `dev`，再测试新版本。
2. 验收全部通过后，在同一仓库创建或打开 **base 为 `master`、head 为 `dev`** 的发布 PR。在说明中记录已验收 SHA、覆盖范围和结果。新提交进入 `dev` 时，先让负责人验收新的 PR head。
3. 使用负责人的 GitHub 账号，在可信的项目检出中运行下方 PowerShell 命令。脚本核对 PR 来源、当前 head 和 GitHub 预合并的文件树，然后将 `release/owner-tested-dev` 记到已验收提交上。在登记命令末尾加 `-WhatIf` 可只做核对。
4. 负责人确认发布后，使用下方合并命令或 GitHub 页面完成合并。代理代为发布时需获得本次发布的明确授权。合并后核对 GitHub 上 PR 的状态和 `master` 的合入提交。

```powershell
$pr = [int](Read-Host '发布 PR 编号')
$tested = Read-Host '负责人已测试通过的 dev 完整 SHA'
./scripts/agent/record-release-acceptance.ps1 -PullRequest $pr -TestedSha $tested
```

确认发布时，在同一个终端执行。GitHub 会核对 PR 仍指向已验收的 head。

```powershell
gh pr merge $pr --repo Lueyu/CareerPilot --merge --match-head-commit $tested
```

## GitHub 执行的保护

`master` 禁止直接推送、强推和删除。发布须经过 PR、解决所有评审讨论并通过 `release/owner-tested-dev`。只有仓库管理员可在 PR 内执行最终合并，目前为 Lueyu。负责人也是 PR 作者时，可以用实际测试结果登记验收。

负责人验收状态按提交保存。GitHub 的分支规则检查状态和合并权限，登记脚本负责验证同仓 `dev` 来源。自动化测试通过后仍须等待负责人确认。[验收登记脚本](../../scripts/agent/record-release-acceptance.ps1) · [GitHub 规则](https://github.com/Lueyu/CareerPilot/rules)
