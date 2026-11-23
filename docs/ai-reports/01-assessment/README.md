# 评估体系（Assessment）AI 报告提炼

> 提炼自 CareerPilot（agent-interview）仓库 2026-11-16/17 的评估/审计/验收类报告（基线 `25ae24d`）。只整理原文事实，不补充外部知识；文中状态词沿用原文：IMPLEMENTED / TESTED / VERIFIED / PARTIAL / NOT_VERIFIED / BLOCKED。

## 来源文件

根目录（12 个）：
- ASSESSMENT_E2E_TEST_REPORT.md、ASSESSMENT_FIX_REPORT.md、ASSESSMENT_RELIABILITY_AUDIT.md、ASSESSMENT_REMAINING_RISKS.md、ASSESSMENT_REMEDIATION_PLAN.md、ASSESSMENT_RUBRIC_AUDIT.md、ASSESSMENT_UX_AUDIT.md
- CODEX_E2E_TEST_REPORT.md、CODEX_REMEDIATION_PLAN.md、CODEX_REVIEW.md、EXAM_25_FORENSIC_REPORT.md、PROJECT_AND_NEW_ANSWER_FOLLOWUP.md

docs/ 下（7 个）：
- docs/assessment/LEGACY_REPLAY_COMPARISON.md、NEW_ANSWER_AFTER_RESTORE.md、NEW_ANSWER_PREPARATION.md、REAL_NEW_ANSWER_RESULT.md、REAL_RETEST_PENDING.md、REAL_USER_STREAM_BROWSER.md
- docs/CareerPilot_Codex_Project_and_Assessment_Repair_Prompt.md

## 背景与目标

- 产品目标（用户要求）：岗位要求 → 独立诊断 → 具体缺陷 → 针对性学习与实践 → 延迟复测 → 可信画像更新；高分答案应能帮助真实面试，不是“堆术语就高分”。
- 触发事件：考试 #25 先显示 `SCORING_FAILED`（失败题 Q1），用户手动重试后成功，但认为回答质量很差仍得到部分分，怀疑 Rubric 过宽或评分器未严格执行标准。
- 修复 Prompt（docs/CareerPilot_Codex_Project_and_Assessment_Repair_Prompt.md）规定两个顺序工作包：A=完成 Project Autopilot（自动项目分析/候选记忆/增量分析）；B=考试 #25 根因分析与考试系统评分可信度修复。要求以真实数据、真实模型调用、真实端到端结果验收；不推倒重写，不以测试/页面数量冒充完成。
- 硬约束：先审计后修复；不覆盖用户数据、不删除旧考试、不重建数据库；迁移必须可回滚；历史记录追加修订不静默覆盖；不泄露 API Key/Token/敏感对话；不自动部署个人库、不公开发布。

## 核心发现与结论

1. 考试 #25 事实：COMPLETED，三题 2 / 1.5 / 1，合计 4.5/15 = 30/100；冻结 Rubric 每题仅 `advice/points/max_points=5`，每项为宽泛标题+重复关键词，无权重、锚点、required_evidence、critical_errors。三份原答确有部分正确子点（需求/基础检索、角色分工/资料交接、按任务路由），不能仅因不完整判为错误；真正问题是无法逐项复算“为什么正好是这几分”。
2. REL-01（P1，已确认）：首轮 Q1 LLM span 258 为 OK / 7195ms / 有 usage，随后 Judge span 259 仅记 `judge_failed`；用户手动重试时 `exams._score_questions` 遍历全部题，已成功的 Q2 被重评从 2.0/PARTIAL 变为 1.5/WRONG。原始响应、finish_reason、请求 ID、校验拒绝路径均未保存，不能认定首轮是超时，也不能确定具体 JSON/字段失败原因。
3. REL-02（P1）：评分在 API 请求生命周期内执行，`submit_exam` 持 Workspace/Exam 行锁跨模型调用直到结尾才提交；`server/app/main.py` 无评分任务恢复；首轮 33.315s、重试 68.580s 都在单次请求事务内；现有个人库无考试 attempt/job 表。
4. REL-03（P1）：`llm_judge.py` 多处失败统一返回 None，`exams.py` 只记 `judge_failed`；无 transport/HTTP/截断/JSON/schema/citation 分类，无安全分类、原始响应、usage/request metadata 留档。模型 200 不等于有效成绩。
5. REL-04/05（P1）：无 question_version / rubric_version / prompt hash / model config fingerprint；`ExamAnswer` 只保留最新文本，无 revision 历史；旧 Judge 把答案静默裁到 2,000 字符、参考只取前 500 字符（三题完整参考 26,985/19,625/11,121 字符）、reasoning 只留 600 字符。
6. Rubric 专项（RUB-01~06）：`compile_rubric` 对旧标准确定性编译并标 `LEGACY_UNCALIBRATED`；新 `judge_frozen_answer` 按 criterion 0–4 档、冻结整数权重计算总分并应用预设 cap；任何非零分/关键错误要求原答逐字连续引用；MCQ boolean answer_index 可冒充数字，0/负数/无穷满分此前未校验；`answer_gen.py:626`、`question_generation.py:297` 只保存旧的 key_points 标题/关键词结构。
7. 旧评分器可被利用（CODEX 隔离函数验证）：否定句“我不懂幂等，也绝对不应该重试”被判满分；Rubric 无 keywords 时 `banana` 满分；同题正确证据 1/2/5/10 条 → level 3.1/3.7/5/5、confidence .28/.36/.60/1.00（重复可刷满级）；正常选题倾向反复取同域最低 ID。
8. 核心链路断裂（CP-03，P0）：`server/app/db.py` `autoflush=False`，`session.add(Evidence)` 后先查询、最后才 flush；`planner.py` 只读技能等级不读本题 missed_points。真实 RAG 0 分考试后 Evidence=1，但 8 个 SkillState 仍 UNKNOWN/coverage 0，主任务分到 Agent Evaluation/架构/工具调用，`why_ref=EV-0` 非真实证据 ID。
9. 结果真实性混用（CP-01，P0）：零证据真实空间仍显示 3 条示例任务、2 条待审核面经、差距 1.2 和“所有已评估能力达到目标线”；语音未采音 2.2 秒后生成固定答案；StrictMode 下一次点击创建 exam1+exam2 两场 ACTIVE；评分失败/学习失败提示可能冒充成功。
10. 公开与部署缺陷（CP-04/09/10/13 等）：privacy=FAIL 草稿仍调用 write_file/create_pr，构建 failure 仍 merge 并标 PUBLISHED，回滚只建 PR 就标 ROLLED_BACK；全新库直接 migrate 报 workspaces 不存在、模拟容器缺 BANK_PATH 时 seed exit0 但题数 0；备份恢复遇 SQL ERROR 仍 exit0 并称恢复完成；`get_github_token` 未 await 致 Authorization 使用协程对象字符串；记忆 `{content: JSON字符串}` 被当 list 检测而丢弃。
11. 真实模型对照（LEGACY_REPLAY_COMPARISON）：历史 2/1.5/1=4.5/15；当前旧 judge 重放 3/1.5/0=4.5/15（3 次真实调用、题目分配已不同）；新 Judge + AI_DRAFT v2 标准 1.375/1.125/0.5=3/15（20 分，3 次调用一次有效）。总分相同不等于评分稳定；分数更低/非整数不等于更准确；两版同时更换标准与协议，单次三题不足以证明校准。
12. 真实新答（REAL_NEW_ANSWER_RESULT）：用户就“统一接口与流式边界”作答，得分 13.8/100（0.6875/5），四项档位 1/0/1/0，无关键错误上限；第一次调用 90.011s 超时、第二次 52.851s 成功、8023 tokens（超时请求 usage 未知）；生成任务 4/5/6 与目标 2/3/4；能力域 6 有效覆盖 1→2，等级仍 UNKNOWN；原 task1 仍 PENDING。
13. 真实教学（REAL_RETEST_PENDING）：task1「补强：统一接口与能力边界」source=llm，1 次调用 / 40.892s / 11,989 tokens，task 仍 PENDING，无新成绩；教学内容为模型生成、未人工检查，不证明掌握。
14. 历史可追溯缺口（B-HISTORY，BLOCKED）：#25 首轮 Q1 原响应、校验路径、prompt/config 哈希未保存，当前配置重放不能称精确复原首次失败；旧报告只对应本场 exam 且无总分，长期修订报告版本缺失。
15. 前端 UX（ASSESSMENT_UX_AUDIT）：`Diagnosis.tsx` 最后编辑仅 sessionStorage、换题才保存；502 后 `setPhase('quiz')` 回可编辑态但服务端已冻结；null 能力域 ratio 被 `null*100` 画成 0；参考答案 HTML 直接进 `dangerouslySetInnerHTML`；无题目歧义反馈入口。

## 已落地与已验证

- FIX 报告（2026-11-17）：实现版本冻结、答案修订、独立 Worker 逐题评分、有限恢复、原子报告发布、逐项证据、学习关联与新题复测约束。新增表 `exam_answer_revisions`、`assessment_scoring_runs`、`assessment_scoring_steps`；增量列 Exam.generation/active_run、ExamQuestion.creation_snapshot、ExamAnswer.revision；迁移 `006_assessment_reliability.sql`（先 bootstrap 建缺表再补列）。
- API 契约：提交立即 202 返回 `{exam_id, task_id, status, report_url}`；状态 SCORING / SCORING_RETRY_WAIT / SCORING_FAILED / NEEDS_REVIEW / COMPLETED；未全部成功不发布正式总分/Evidence/画像；传输异常最多 3 次自动尝试、结构修复最多 1 次、最终发布另有 3 次上限；`can_retry` 只针对未成功题；非 OFFICIAL 任务不进正式发布分支。
- 后端最终全量 **395 passed / 0 failed / 0 skipped / 191.82s**（全新库 `careerpilot_suite_20261117_051256_0de19e_test`）；首轮 377/2 failed 因旧夹具缺可评分 Rubric，补齐明确标准后原断言未放宽。前端最终 **114/114**，typecheck 与 build 通过（仅既有大 chunk 提示）。
- 真实 DB/OS 故障注入：kill 评分 Worker 后 Q1 attempt1 保留、Q2 新 attempt2 保留 OUTCOME_UNKNOWN，最终 2 Evidence/1 Snapshot；API 重启/HTTP 断连后原 Worker 继续、无重复副作用；outbox 持久结果在新租约下无模型恢复；发布失败最多 3 次手动恢复只发布成功题。
- 真实模型证据：隔离库 `careerpilot_assessment_live_20261117_045254_test`，原答哈希一致、配置不变（gateway / deepseek-v4-flash），3 次调用一次成功：Q1=1.375、Q2=1.125、Q3=0.5，总 20.0，约 92 秒；生成 3 Evidence / 3 任务 / 1 画像快照，各域 1 独立观察仍 UNKNOWN。
- 浏览器验收（Playwright 真实浏览器，模型为夹具）：关闭/重开浏览器后成功题保留、无伪总分，重开显示 COMPLETED、2 题 5/10 与未校准说明；歧义反馈提交后保存待人工审核、成绩未变；无 console error（仅 2 条 React Router future-flag warning）。
- 备份/迁移：pg_dump 恢复 42 表完整哈希、39 序列、78 外键、3 个 outbox 附件逐字一致且无再次模型调用；迁移 006 重复执行幂等、空数据可回滚、存在 ScoringRun 时回滚主动拒绝；个人库从未运行迁移。
- 新答恢复验收（NEW_ANSWER_AFTER_RESTORE）：46 表/43 序列/94 外键一致；question55→exam3→answer-revision5→scoring-run3/step5→Evidence4→task4–6→goal2–4 完整关联；原答 SHA-256 `f97b03c2e53a3b2954ccc66890b501035d6c29042d3410aab3b76af5b1720de1` 逐字匹配；两个 outbox 缓存文件（run2/run3）复制前后 SHA-256 一致。
- 前端测试：新增 `web/tests/assessment-ux.test.mjs` 16 项通过；`test_assessment_feedback.py` 3 passed；`test_core_scoring.py` + `test_project_intelligence.py` 31 passed（后台流程夹具）；followup 阶段后端全量 **446/446**（271.13s）、前端 **149/149**，typecheck/build 通过。
- 后续修复（PROJECT_AND_NEW_ANSWER_FOLLOWUP）：模型适配器新增安全响应阶段诊断（只记录是否收到响应头、状态/请求 ID/类型/时序，不保存敏感 headers/正文，不改 payload）；报告标题改用实际考试名称，13.8 不再取整为 14。

## 遗留风险与待办（原文提到的）

- A-LIVE（P1，BLOCKED）：Project Autopilot 完整模型分析 21 模块最低 66 次调用超预算；用户授权上限提到 500 后本轮 7 次调用 1 成功/6 超时，累计 11/500，133→134 成功检查点；无完整报告/候选记忆；不得擅自提高预算、换模型或跳过源码。
- B-CALIBRATION（P1，PARTIAL）：三题 v2 标准与 39 份 AI 样例（`human_reviewed=false`、`expected_score=null`、`is_gold_standard=false`）未人工评审；评分一致性/排序/招聘效度未证明；待人工审定标准与样例、盲评/复评。
- B-RETEST（P1，NOT_VERIFIED→部分推进）：真实旧答→v2 评分→证据→任务→真实教学已发生，真实新答也已评分（13.8/100），但仍无“学习后新题再评分”的延迟保持证据（1/3/7 天到期作答）；原 task1 仍 PENDING。
- B-HISTORY（P1，BLOCKED_BY_MISSING_HISTORICAL_ARTIFACT）：#25 首轮 Q1 原响应/校验路径/prompt/config 哈希不可恢复，只能保留证据缺口。
- B-LEGACY（P2，PARTIAL）：全题库仍有旧宽泛 Rubric；可编译者标 LEGACY_UNCALIBRATED，缺标准/待审题拒绝开考；升级前无新冻结合同的在考/失败答卷拒绝混用新 Judge；新标准不得静默套给旧分数。
- B-OUTCOME-UNKNOWN（P2，TESTED）：fencing 阻止迟到覆盖、outbox 可无模型恢复；但进程在 provider 已执行而本地未存前死亡时，无法保证外部只计费一次（未实现供应商幂等）。
- B-STORAGE（P2，VERIFIED/PARTIAL）：完整 DB+私有 outbox 恢复可读；普通设置 JSON 导出仍非完整备份；私有原响应/outbox 无自动保留期限/归档管理，不能公开上传。
- B-OPERATIONS（P2，TESTED/NOT_DEPLOYED）：本地 Windows 进程与浏览器验收通过；个人库未执行 006、个人 API/Worker 未重启；多主机压力、跨 OS、长期服务稳定性未验证；部署需用户明确批准。
- B-TASK-CAP（P2，PARTIAL）：按低分优先最多安排 3 项任务（planner 默认 limit3），其余缺口只留在报告；Q1“高并发”为 missing 但未获任务；未安排不等于掌握。
- B-ADJACENT-JOBS（P2，PARTIAL）：项目分析与正式评分已独立 Worker，但部分相邻生成入口仍在 API 进程，不能对全产品宣称“关页面/重启均无影响”。
- B-REVIEW（P2，IMPLEMENTED/TESTED）：题目歧义反馈可提交/分页读取/刷新保留、进入待人工审核、不改分；人工审核处理/关闭反馈的完整工作台尚未实现。
- REL-06~11（P2）：最后编辑仅 sessionStorage 的持续保存与语音最终片段时序、迟到结果拒绝、事件时间与动作顺序语义、画像无历史快照、计划上限未排期说明等已识别风险仍需回归/产品流程。
- 其他未验证：真实 GitHub 增量模型全链、人工金标准、多次评分一致性、长周期学习收益、Docker 全新镜像全栈启动/容器级恢复、PDF/DOCX/图片 OCR 真实上传、真实公开发布与回滚。
- 明确未做（原文约束）：不向个人库迁移/部署、不覆盖考试 #25 分数（保持 4.5/15 历史事实）、不换模型、不公开发布、不回滚任何新增活动；`ASSESSMENT_REMAINING_RISKS.md` 明确：不能据现有报告将整项任务标为完整 VERIFIED，真实完整“学习→新题→重新评分”闭环在 11-17 时点尚未通过。
