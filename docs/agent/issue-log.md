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

## H8 · 2026-11-28 · DONE
- 结论：五处嵌套输入显式校验，实际收包/深度边界；题库最多200一页，全集成功后展示。95专项/前端290与实际435题三页、筛选收藏、失败重试通过；独立审核绑定最终冻结head。
- PR：https://github.com/Lueyu/CareerPilot/pull/63
- 验证 SHA：bf6d31e90097b304c3d774a800c479c373171ddc
- 证据：.agent-runs/20261128-wave1/h8

## F6 · 2026-11-28 · 公开版初始化补修
- 真实全新公开镜像只建ORM表，缺少独立SQL恢复控制表，显式登记报relation不存在。健康200同时受保护API503的原始证据保留。
- 公开启动在原有专用目标/安装标记校验及事务内复用既有DDL，仅增加两行。建空表不写NORMAL，不改变恢复隔离与离线确认规则。
- 真实PostgreSQL专项11项通过；精确新镜像的空表/API503、显式登记退出0/API401、重复初始化及登记元数据不变已验证，源码/SQL/JS字节核对通过。
- PR：https://github.com/Lueyu/CareerPilot/pull/64；源码：eae9384c675c74cbe26473af60c0ed258f25789a。原PR57与原备份恢复报告保留，非作者最终审核和合入以本补修回执为准。
- 证据：.agent-runs/20261128-wave1/f6-followup/。H3保留原源码树，待本补修合入后从新dev重放并验收。

## H3 · 2026-11-28 · DONE
- 结论：统一不可信资料权限合同和严格输出结构，保存可疑指令提示及完整解析原文，个人/公开审核可刷新重开；修正并发导入trace编号，补齐真实精简公开镜像共享依赖及恢复SQL。
- 验证 SHA：a369f3d9a4704fdb6595a60c6a9b8cdae52ba611
- 证据：.agent-runs/20261128-wave1/h3/product-browser.json
- 遗留/后续：未调用真实模型，词法提示不证明攻击抵抗率；原生租户功能关闭，旧Bank组件由组件与受控网关测试覆盖。
- PR：https://github.com/Lueyu/CareerPilot/pull/65；独立源码审核与最终精确head续审记录见 .agent-runs/20261128-wave1/h3/review/。

## H4 · 2026-11-28 · DONE
- 结论：个人与公开导入共用受限短生命周期解析进程，ZIP/PDF/像素与协议闸前置；Windows Job和Linux rlimit/进程组负责资源及清理，保留合法文件与来源。
- 验证 SHA：9e3197df70bd3f2bdbec4b8b8a8e8d3e2a44e1ee
- 证据：.agent-runs/20261128-wave1/h4/product-browser.json
- 遗留/后续：资源隔离不等于权限沙箱；Linux按进程限额与Windows Job整组限额分开说明，个人实例未升级，Linux可选OCR未实测。
- PR：https://github.com/Lueyu/CareerPilot/pull/66；最终非作者精确head记录与合入回读见 .agent-runs/20261128-wave1/h4/review/ 和 merge-receipt.json。

## A2 · 2026-11-28 · DONE
- 结论：PR37已在dev；补齐有界中英主题与清单意图，保留复杂前后文和任务。独立审核发现前置SQL丢失后修正；同六文件128通过无跳过，30条固定语料联合20/30至30/30。精确源码镜像三条实际UI请求完成、102命中100展示、来源快照和隔离保留。
- PR：https://github.com/Lueyu/CareerPilot/pull/67
- 验证 SHA：6746b6bb388eb3ccd7543821dd4bf1858a03deba
- 证据：.agent-runs/20261128-wave1/a2/product-browser.json

## G4 · 2026-11-28 · DONE
- 结论：存活healthz与就绪readyz拆分；限时SELECT1及恢复闸，个人/公开/租户探针和反代迁移。作者与非作者各自64项通过，另14项独立风险探测通过；两个精确镜像真实DB停止后200/503、恢复200/200，Caddy语法验证通过。旧静态夹具基线交F7，标准controller false保留，未部署个人实例。
- PR：https://github.com/Lueyu/CareerPilot/pull/68
- 验证 SHA：58b7bed906cc3c81037292411f94de6d11c45e75
- 证据：.agent-runs/20261128-wave1/g4/product-public/g4-health-receipt.json

## F7 · 2026-11-29 · DONE
- 结论：统一安全错误字段与服务端关联编号，切断业务及兜底二次失败异常链，保留取消、流中止与重试票据。后端203、前端298项通过，两个精确镜像与主服务坏行修复验证通过；当前PR等待精确提交非作者终审，不涉及个人发布。
- PR：https://github.com/Lueyu/CareerPilot/pull/69
- 验证 SHA：d2e8286a4e9873f4f003363b21de8a1c76c3c09c
- 证据：docs/known-issues/reports/wave1/F7.html
