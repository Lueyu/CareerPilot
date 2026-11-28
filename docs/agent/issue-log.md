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

## G6 · 2026-11-28 · DONE
- 结论：个人启动拒绝默认和明显弱凭据；首次随机私有文件原子发布且重跑稳定，Windows实际DACL回读；前端运行时登录不嵌令牌。作者后端125、备份5、预览11、前端284通过；父代理冻结源码实际应用/浏览器与PG16回归77通过。独立审核见本PR，范围重叠不加总。
- PR：https://github.com/Lueyu/CareerPilot/pull/58
- 验证 SHA：5f97694b0d03dd92d8ba907b07700170ba54641d
- 证据：.agent-runs/20261128-wave1/g6/handoff.json
- 遗留/后续：H1负责hash/撤销/轮换/租户与恢复代际；已有弱配置由操作者维护迁移。未改个人实例或既有角色密码；Python控制器未被标为PASS，原生Docker证据单列。

## H1 · 2026-11-28 · DONE
- 结论：持久化hash凭据、到期撤销轮换、稳定租户边界与安装级失败桶；127项核心及18项生命周期、4项CLI、5项恢复、11项适配分次通过，未累加重叠。
- PR：https://github.com/Lueyu/CareerPilot/pull/59
- 验证 SHA：3e88bca91506db173632d8dc10ff8a1ddd7c3dff
- 证据：.agent-runs/20261128-wave1/h1/handoff.json
- 遗留/后续：注册租户真实首次安装需明确离线登记；16项基线调用者失败已分配E2补修/F7/F8和安全夹具验证，不在此冒称整波通过。

### H1 独立审核修复 · 2026-11-28
- 首轮 af82cde 非作者复核发现轮换与空间删除、同名重建的竞争导致身份变化，原FAIL与反例保留。
- 修复在事务内按原编号锁住工作空间记录，避免名称被重新使用；修复源码3febcde33427bb0b46984388b5e7adb9eb95de4d。
- 新回归在旧实现真实失败（原编号2、后继编号4），修复后完整生命周期20项通过，ruff与差异检查通过。
- 证据：.agent-runs/20261128-wave1/h1/r1-fix/；PR59新冻结提交需另行独立复核。

## E2 · 2026-11-28 · DONE
- 结论：补齐面试题整理的BillingScope；真实隔离账本验证预留、结算与缓存不重复计费。相关175项通过，测试配额和恢复夹具遵守当前契约，生产闸门未放宽。
- PR：https://github.com/Lueyu/CareerPilot/pull/60
- 验证 SHA：4fe46c85e8ddb193db70937ceb5ee20e2e7fb1d9
- 证据：.agent-runs/20261128-wave1/e2-followup/handoff.json
- 遗留/后续：F7仍须修复取消传播，F8仍须复查deadline清理；真实供应商与个人实例未验证。

## H2 · 2026-11-28 · DONE
- 结论：模型出站共享策略绑定实际TCP已验证IP，保留原URL/Host/TLS及定价凭据身份；公网HTTPS默认与部署侧精确例外，个人/注册/租户/反馈模型入口覆盖。112项、162项及最终34项分次通过，范围重叠不加总；独立终审以本PR最终head为准。
- PR：https://github.com/Lueyu/CareerPilot/pull/61
- 验证 SHA：630097d5c0350ade03c5da5a0be68f90a508a512
- 证据：.agent-runs/20261128-wave1/h2/handoff.json
- 遗留/后续：590组合580通过10失败，9项夹具问题已定向修复，已登记cancelled/timeout问题仍由F8闭环。真实供应商、个人部署未验证；本地模型须显式部署origin授权。

### H1 容器CLI交付补修 · 2026-11-28
- H8精确源码4d6bd63的隔离镜像实际调用token_lifecycle.py时返回Errno2；candidate_delivery.start已有明确容器调用，因此补齐镜像文件清单。
- 独立补修分支从dev c4d9ad3建立，仅增加server/Dockerfile一条COPY；补修源码70ed7f72626b772a82874f1049dadd122485497b，PR62。
- 旧实际镜像帮助命令退出2；修复镜像来自精确Git server树，禁网帮助命令退出0。核对本任务数据库容器和网络后，显式enroll-env及list均退出0，元数据编号一致且不含原始token。
- 原H1报告、PR59和生命周期证据保留，并扩展镜像与源码的教学说明。未改变API/worker/重启登记语义，不修改个人实例或数据；独立审核与合入以h1-followup最终回执为准。
- 证据：.agent-runs/20261128-wave1/h1-followup/；H8须跟进补修后的dev再做最终确切版本浏览器核验。
