# CareerPilot 面经与补题工作台

用户已于 2026-11-16 确认采用第一种设计并授权开发：统一生成任务、API 与外部聊天两种执行方式、统一审核入私有题库。通用问题直接回答；项目问题结合本人项目，不能将仓库观察当作本人贡献。

## 范围和体验

题库提供明确入口。用户可以粘贴面经，或从已有导入会话继续优化；也可以选择系统建议的补题批次。建议依据题库覆盖、已登记项目和学习任务，只解释内容缺口，不宣称测出了能力弱点。用户选择题量（1–20，默认 5）、技能、项目和执行方式。

项目可来自已有 GitHub 项目或手工登记。个人资料包含 background、role、responsibilities、stack、decisions、challenges、outcomes、boundaries。用户可以维护真实经历，空白字段不由模型补成事实。生成时冻结资料快照，分别标记用户陈述和仓库观察；扫描指纹不冒充 Git commit SHA。

创建生成任务只准备输入和 prompt。API 模式需要显式启动，记录运行、失败和输出；外部模式显示完整 prompt，允许复制到聊天后粘贴整个回复，不要求用户手写 JSON，也不暗中调用 API。两条路径共享 schema、校验、答案排版和审核。错误保留原文并可重新粘贴；结果通过校验后保存为 ImportSession / ImportItem。

审核页显示原题、改写题、参考答案、追问、评分要点、来源与项目事实引用。可保存真实编辑，再逐题或批量通过。缺乏必要项目资料的草稿不得作为本人已做经历入库。已批准内容进入当前工作空间私有题库；精确重复合并，已有题的项目变体关联 canonical_question_id。学习材料不产生 Evidence，也不提高能力等级。

## 模块与固定接口

- `services/project_context.py`: `build_project_context(session, workspace_id, project_ids) -> list[dict]`。每项 `{id,name,kind,profile,facts:[{id,text,source}],has_personal_context,snapshot_fingerprint}`。profile 用户输入，facts ID 稳定且快照保留；无权项目抛 ValueError。
- Projects API：`POST /projects` 兼容旧仓库创建，新增 `{name,kind:'manual',profile:{...}}`；`PATCH /projects/{id}/profile {profile:{...}}`；列表/详情返回 kind/profile。手工项目不可触发仓库扫描。
- `services/question_generation.py`: `build_prompt(snapshot,batch_id)->str`; `parse_response(raw,snapshot,batch_id)->list[dict]`; `render_item(item,snapshot)->dict {answer_html,rubric}`；`generate_response(prompt)->dict {text,model,...}` 异步且失败显式抛异常。解析错误用 ValueError，纯解析不调用模型。
- snapshot: `{kind:'import'|'supplement',count,skill_keys:[...],skills:[{key,name}],project_context:[...],source_text,source_questions:[{id,title,canonical_question_id}],purpose}`。
- 输出协议：`{schema_version:1,batch_id,items:[{title,original_question,skill_key,question_mode:'general'|'project'|'needs_context',project_id:null|int,fact_refs:[str],answer:{tldr,elevator,analogy,examiner:[str],steps:[{title,detail}],how_to_answer,pitfalls:[str],followups:[{q,a}],key_points:[{title,detail}],terms:[{term,desc}]},project_answer:'',hypothetical_answer:'',missing_context:[str],source_question_id:null|int}]}`。
- `QuestionGeneration` 独立表：workspace、kind、mode、status、request_json、input_snapshot、prompt_text、raw_response、error、import_session_id、request_key、created_at。Question 与 ImportItem 新增 generation_meta JSONB，可空且兼容历史数据。
- `POST /question-generation`：`{kind,mode:'api'|'external',count,skill_keys,project_ids,source_text,purpose,source_import_id?,request_key?}`。创建任务不调用模型。
- `GET /question-generation` -> `{items:[task]}`；`GET /question-generation/suggestions` -> `{items:[{id,title,reason,kind,count,skill_keys,project_ids,purpose}]}`；`GET /question-generation/{id}` -> task。
- task: `{id,kind,mode,status:'READY'|'RUNNING'|'REVIEW'|'FAILED',prompt_text,input_snapshot,raw_response,error,import_session_id,created_at,review?:existing_import_review}`。
- `POST /question-generation/{id}/run {}`：仅 API 模式；后台执行，立即返回任务。重复启动 RUNNING 不重复执行；已有结果不能重跑覆盖。
- `POST /question-generation/{id}/result {text}`：仅 external；解析入草稿，失败保存原文与错误；已导入同一文本幂等返回，已入审核不可重复生成覆盖。
- `PATCH /question-generation/{id}/items/{item_id} {item:<完整协议单题>}`：仅 DRAFT，重走校验排版，持久化。
- 复用 `POST /bank/imports/{id}/approve {item_ids:[...]}` 与 reject。generated import 审核不可暗中生成答案；generated 草稿通过前需完整合法；普通原有导入保持兼容。
- review items 新增 `generation_meta`（含 item 结构、task_id、source、快照引用）、question_id；题库详情提供 generation_meta。

## 可靠性边界

工作空间隔离覆盖读取、资料、快照、草稿编辑与审核；原文当数据，不能指令系统泄露信息或执行工具。限制输入长度和题数，禁止虚构真实公司来源。重启后运行中任务标记失败可恢复，审核结果不依赖内存。请求/重复审核幂等，变体不增加独立题数。发布开关继续关闭。大模型文本仍需用户审核，结构与引用校验不能证明技术答案全对。

## 验收

测试覆盖一般题、项目题、缺资料、越权引用、跨空间、外部解析零 API 调用、错误可修复、草稿编辑保存、重复提交/审核、canonical 关联、能力证据不变与重启持久化。先备份原数据库与已有 diff；测试使用独立数据库。真实模型单独验证并标明费用和结果，不能将夹具测试称为真实模型验证。最终迁移保留用户已有项目和资料，提供操作指南及验证报告。
