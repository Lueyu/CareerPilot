# E2E 测试报告

> 版本：v3.2（Backend Completion）· 测试日期：2026-11-16
> 方式：API 级（curl / 直连 DB 断言）+ 浏览器级（Chromium 实际操作 UI）
> 结论：**8/8 场景通过**，全部为真实链路（无 Mock、无假数据路由）。

## 场景总览

| # | 场景 | 级别 | 结果 | 关键提交 |
|---|---|---|---|---|
| E1 | 鉴权与工作空间隔离 | API | ✅ | db182f4 |
| E2 | 面经导入端到端（图片 OCR → 审核 → 入库） | API+UI | ✅ | 4bb722f / 0c65882 |
| E3 | 独立诊断闭环（选题→作答→评分→画像→任务） | API+UI | ✅ | 54abffb |
| E4 | 深度解答生成（LLM，电梯稿/类比/追问带答案） | API+UI | ✅ | 0c65882 |
| E5 | 诊断深度报告（能力域得分 + AI 逐题剖析） | API+UI | ✅ | 62d8699 |
| E6 | 公开内容发布流水线（校验→dry-run→发布→回滚） | API+UI | ✅ | 0276361 |
| E7 | 进化实验室（候选→评估→批准→策略生效→回滚） | API+UI | ✅ | 4a37443 / 16ee7e3 |
| E8 | 学习中心（教学内容生成 + 7 种求助 + 缓存） | API+UI | ✅ | 22c9c27 |

单元测试（确定性引擎，`server/tests/test_engines.py`）：**19/19 通过**（benchmark_jd 解析与上调规则 / scorer MCQ+开放题 / feedback 结构 / followups 上限 / llm_judge 确定性 verdict+空作答短路+坏 JSON 拒绝 / teaching JSON 解析+规则兜底 / 隐私扫描 / 低风险闸门 / evolution 四分支决策规则）。

---

## E1 鉴权与工作空间隔离

- 无/错 token 访问业务接口 → `401 {"detail":"invalid token"}`（实测）。
- `X-Workspace: 不存在` → `404 workspace 'xxx' not found`（实测）。
- 所有业务路由经 `get_workspace` 依赖强制 workspace 过滤；`WorkspaceDelete` 需输入 slug 确认，不匹配 422。
- 多工作空间数据隔离由 FK + 查询过滤保证（单机 MVP 默认 `demo`）。

## E2 面经导入端到端

1. 上传 JPG 图片（真实截图）→ 后台流水线 5 阶段真实进度（`meta.progress.step/note` 落库，前端 600ms 轮询渲染）。
2. RapidOCR 本地识别 → 提取题目（置信度标注）→ 技能映射 → 查重（重复问法并入 canonical variants）→ 生成草稿答案。
3. 审核页逐题「通过」→ 入库 `USER_PRIVATE` 私有题（不进公共题库）；全部处理完才会话置 APPROVED。
4. 单题「驳回」→ REJECTED（实测 remaining 计数正确）。
5. 假进度修复后：进度条与后端实际阶段一一对应（不再有定时器假进度）。

## E3 独立诊断闭环

1. `POST /exams`（自适应选题：差距大×权重高×证据少优先，selection_reason 可解释）。
2. 作答（MCQ/文本）→ 提交 → 状态机 `SUBMITTED→TRANSCRIPT_CONFIRMED→FROZEN→SCORING→COMPLETED`（冻结快照后评分）。
3. 开放题 LLM 评分官（judge-v1）：verdict 由 points/max 比例确定性判定；LLM 不可用自动回落 rule-v1 并诚实标注。
4. 评分失败 → `SCORING_FAILED`（不给假分），报告页可「重新评分」。
5. 完成后自动：写证据 `EV-X{exam}-{n}` → 能力画像重算 → 学习任务生成（1/3/7 复测链）。
6. 放弃/超时：`ABORTED`（幂等 409）/ `EXPIRED`（懒判定）均实测。

## E4 深度解答生成

- 导入题目触发深度解答（LLM 网关 deepseek-v4-flash 实测 26-50s）：一句话回答 / 30 秒电梯稿 / 类比（含失效之处）/ 面试官视角 / 详细拆解 / 回答脚本 / 常见坑 / 追问带参考答案 / 评分要点 / 术语卡。
- LLM 未配置时回落规则引擎（结构完整、诚实标注「规则生成」）。

## E5 诊断深度报告

- 能力域得分条（薄弱在前）+ 确定性诊断结论（strong/mid/weak + 优先行动）。
- AI 深度诊断（缓存于 exam.summary，二次加载瞬时）：结论/强项/薄弱项(gap)/行动清单/逐题 why+how。
- LLM 幻觉防护：缺题自动最小材料补齐重试（`retried` 标记）；占位键被 `_sanitize_pq` 丢弃。

## E6 公开内容发布流水线

1. 导入仓库 → 真实 GitHub 扫描（294 文件/111 commits/语言分布）→ 104 发现项 → 能力矩阵（VERIFIED/HIGH/UNVERIFIED_CLAIM）。
2. 资产更新：生成 Markdown 草稿 + 风险分级（隐私/事实/许可/Markdown 四重校验，fact WARN 不阻断但降级自动合并资格）。
3. dry-run PR（`PB-x DRY_RUN`，无真实写入）→ 批准发布 → GitHub Actions 构建 → PUBLISHED；回滚 → `ROLLED_BACK`。
4. 红线：HIGH 风险强制人工；`auto_merge` 规则关闭时一律仅创建 PR。

## E7 进化实验室

1. 真实信号收集（考试均分/复测完成率/任务完成率）。
2. 样本不足（<3 次考试）→ 诚实拒绝造候选（实测 reason 提示）。
3. 第 3 份样本后：`EVO-0001 降难度优先` PROPOSED → evaluate（确定性投影指标，sample<5 → INSUFFICIENT_EVIDENCE）→ approve → ACTIVE。
4. 策略生效：新考试 selection_reason 含 `EVO-0001`，选题偏移实测生效。
5. 回滚 → ROLLED_BACK（基线恢复）。

## E8 学习中心

- 今日任务卡（为什么学/耗时/完成标准）→ 任务详情 7 步教学内容（LLM 按题库材料生成 26.8s / 规则兜底）。
- 7 种学习求助（更简单/类比/例子/结合项目/代码/图解/还是不懂），结果缓存（二次 0.08s 命中）。
- 完成任务 → 复测安排 1/3/7 天。

## 已知边界（诚实声明）

- E2E 以「本地真实数据 + 真实 LLM 网关」执行，未接入外部 mock 服务；GitHub 发布链路在 dry-run 与真实 PR（#3/#4，公开内容模块早期实测）双路径验证过。
- 图片上传仅 PDF/DOCX/MD/TXT/JPG/PNG；`addFiles` 前端白名单与后端分发均已覆盖（曾修复白名单漏放 jpg/png 的缺陷，见 0a52b39）。
