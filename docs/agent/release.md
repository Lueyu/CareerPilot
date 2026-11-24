# 将验收过的 dev 发布到 master

功能开发与正式发布分两次审核。贡献者从上游最新的 `dev` 拉分支，改动通过测试后向 `dev` 开 PR。维护者独立审核最终提交，处理意见，复验改动，再合入 `dev`。日常交付给出合入后的完整 SHA、测试结果和可体验的入口。

## 负责人验收并发布

1. 测试前确认 `dev` 已包含当前 `master`。如果上次发布产生的合并提交尚未进入 `dev`，先在从最新 `dev` 建的分支上合并 `master`，通过审核后的 PR 同步回 `dev`。负责人 Lueyu 按自己的节奏测试集成版本，从实际运行的版本或部署回执记录完整 SHA 和结果。
2. 验收全部通过后，在同一仓库创建或打开 **base 为 `master`、head 为 `dev`** 的发布 PR。在说明中记录已验收 SHA、覆盖范围和结果。新提交进入 `dev` 时，先让负责人验收新的 PR head。
3. 最终合并由 Lueyu 负责。GitHub 的绿色状态跟随提交，同一 SHA 可以出现在多个 PR 中。负责人在合并当下须核实当前 PR 确实来自同仓 `dev`，验收由本人针对这个 PR 和 SHA 登记。下方命令用负责人的账号在可信检出上重新核实来源、主干版本和预合并文件树，当场登记并按该 SHA 合并。代理代为执行须获得本次发布的明确授权。
4. 合并后核对 GitHub 上发布 PR 的状态和 `master` 的合入提交。修复发现的问题仍向 `dev` 开 PR，负责人测试修复后的集成版本。

```powershell
$pr = [int](Read-Host '发布 PR 编号')
$tested = Read-Host '负责人已测试通过的 dev 完整 SHA'
./scripts/agent/record-release-acceptance.ps1 -PullRequest $pr -TestedSha $tested -WhatIf
```

上一步只预演。负责人确认发布时，在同一个终端执行整个代码块。登记失败会终止本次合并。

```powershell
& {
    $ErrorActionPreference = 'Stop'
    ./scripts/agent/record-release-acceptance.ps1 -PullRequest $pr -TestedSha $tested
    gh pr merge $pr --repo Lueyu/CareerPilot --merge --match-head-commit $tested
    if ($LASTEXITCODE -ne 0) { throw 'GitHub 未完成合并，请检查发布 PR。' }
}
```

## GitHub 执行的保护

`master` 禁止直接推送、强推和删除。发布须经过 PR、解决所有评审讨论并通过 `release/owner-tested-dev`。GitHub 要求发布分支包含最新主干。只有仓库管理员可在 PR 内执行最终合并，目前为 Lueyu。负责人也是 PR 作者时，可以用实际测试结果登记验收。

负责人验收状态按提交保存。GitHub 的分支规则检查状态和合并权限，登记脚本负责验证同仓 `dev` 来源。自动化测试通过后仍须等待负责人确认。[验收登记脚本](../../scripts/agent/record-release-acceptance.ps1) · [GitHub 规则](https://github.com/Lueyu/CareerPilot/rules)
