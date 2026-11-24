[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidateRange(1, 2147483647)][int]$PullRequest,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$TestedSha
)

$ErrorActionPreference = 'Stop'
$repo = 'Lueyu/CareerPilot'
$TestedSha = $TestedSha.ToLowerInvariant()

function Read-GitHub([string]$Path) {
    $response = gh api $Path
    if ($LASTEXITCODE -ne 0) { throw "无法读取 GitHub 数据：$Path" }
    $response | ConvertFrom-Json
}

if ((Read-GitHub 'user').login -ne 'Lueyu') {
    throw '请使用负责人 Lueyu 的 GitHub 账号登记验收。'
}
$pull = Read-GitHub "repos/$repo/pulls/$PullRequest"
if ($pull.state -ne 'open' -or $pull.base.ref -cne 'master' -or
    $pull.head.repo.full_name -ne $repo -or $pull.head.ref -cne 'dev') {
    throw '验收仅适用于同仓 dev 到 master 的开放发布 PR。'
}
if ($pull.head.sha -ne $TestedSha) {
    throw '发布 PR 的当前提交与实际验收的 SHA 不一致。请核实待发布版本。'
}
if ($pull.mergeable -ne $true -or -not $pull.merge_commit_sha) {
    throw 'GitHub 尚未生成可合并的发布预览。请检查 PR 的合并状态。'
}

$testedTree = (Read-GitHub "repos/$repo/git/commits/$TestedSha").tree.sha
$preview = Read-GitHub "repos/$repo/git/commits/$($pull.merge_commit_sha)"
if ($preview.parents.Count -ne 2 -or $preview.parents[0].sha -ne $pull.base.sha -or
    $preview.parents[1].sha -ne $TestedSha -or $preview.tree.sha -ne $testedTree) {
    throw 'GitHub 预合并版本与已验收的 dev 内容不匹配。请更新发布 PR 并复验。'
}

if ($PSCmdlet.ShouldProcess("$repo PR #$PullRequest @ $TestedSha", '记录负责人验收通过')) {
    gh api -X POST "repos/$repo/statuses/$TestedSha" -f state=success -f context=release/owner-tested-dev -f 'description=Project owner tested this dev revision' -f "target_url=$($pull.html_url)" --jq '{state,context,target_url}'
    if ($LASTEXITCODE -ne 0) { throw 'GitHub 未接受验收状态。' }
}
