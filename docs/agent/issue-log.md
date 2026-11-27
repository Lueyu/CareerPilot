# 短板补全日志

> 由 issue-solver 技能追加；每项完工记一笔。清单事实源见 KNOWN_ISSUES.md。

## E1 · 2026-11-28 · DONE
- 结论：工作区和同库租约准入+进程执行及等待双界；21项容量独立复验及5个独立行为通过，终态写库超时保留调用证据；审核PASS+NOTES
- PR：https://github.com/Lueyu/CareerPilot/pull/51
- 验证 SHA：13786ea060a3373bbf3ef93f117eab969b029e17
- 证据：docs/known-issues/reports/wave1/E1.html
- 遗留/后续：持久队列/outbox/drain留第5波；本项Semaphore仅进程内；个人实例未部署

## E2 · 2026-11-28 · DONE
- 结论：独立财务账本在联网前原子预留，按冻结配置价格结算；部署共享日预算，未知用量保留预留，超预留暂停当天后续准入。最终作者和独立69项通过，额外4进程抢最后预算通过，前端283项通过。
- PR：https://github.com/Lueyu/CareerPilot/pull/54
- 验证 SHA：4ad8be58b29390a72ff36aec47971f6e65e9f70b
- 证据：.agent-runs/20261128-wave1/e2/review/review.json
- 遗留/后续：未知费用需外部证据对账；价格需显式配置。真实供应商、个人实例未测试或部署。原有timeout/cancelled分类缺陷保留。

## E3 · 2026-11-28 · DONE
- 结论：可信身份准入；读取/写入/模型触发分类，短时令牌桶加UTC+8自然日硬配额，同库多进程原子扣除，429+Retry-After。当前28项通过，既有相关29项carried通过，独立审核结果见本PR。
- PR：https://github.com/Lueyu/CareerPilot/pull/55
- 验证 SHA：e9006eb8efdf94f1a5cb8fe68f451c84a381fd59
- 证据：.agent-runs/20261128-wave1/e3/handoff.json
- 遗留/后续：不同DB不自动汇总；缓存及取消不退款；后续新模型入口需登记分类；个人实例未部署。

## G5 · 2026-11-28 · DONE
- 结论：Fernet版本化密文绑定设置行与字段路径，运行读取逐次认证并拒绝错误凭据回退；显式原子幂等迁移。作者当前257通过1跳过1既有分类失败，另4进程通过；95个G5用例通过，提示校对后现版迁移CLI单项通过。独立审核见本PR。
- PR：https://github.com/Lueyu/CareerPilot/pull/56
- 验证 SHA：958054f48ff9af9c23a380b8b4103dd49e760532
- 证据：.agent-runs/20261128-wave1/g5/handoff.json
- 遗留/后续：外置主密钥需单独保管；旧WAL/dump不保证擦除；没有KMS/自动轮换；个人实例未迁移。

## F6 · 2026-11-28 · DONE
- 结论：标准age加密与版本化清单；PENDING复制持久化和7日4周3月并集保护；受控新库导入与全入口隔离，UNKNOWN提交保留。作者201项通过，合成PG恢复到已验证隔离库4.89秒，私人镜像打包实测通过；独立审核见本PR。
- PR：https://github.com/Lueyu/CareerPilot/pull/57
- 验证 SHA：ae43c49a01e198c2bd92ef50886b60b055468350
- 证据：.agent-runs/20261128-wave1/f6/handoff.json
- 遗留/后续：真实异地NOT_CONFIGURED；无生产切换与隔离解除，H1需落实新凭据代际；公开镜像既有allowlist缺口未宣称修复。个人实例未变更。
