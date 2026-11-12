# AI Agent 面试题库 v2 重构设计

- 日期:2026-11-13
- 状态:已与用户逐节确认通过
- 关联仓库:Lueyu/agent-interview(master,单文件 index.html,1.26MB,160 题)

## 1. 背景与问题

现状:index.html 单页塞下 160 题(7 板块 + Top10),继续膨胀导致易用性下降;内容混编在
一个巨型 HTML 里,人工增题困难(.fragments 临时流程虽验证了分片思路,但不可持续)。

用户目标:

1. 一页一个面试题,题下可记笔记
2. 可标记熟练程度
3. 按 AI Agent 社招真实考察主题重新分类(RAG / Harness 编排等)
4. 提供一个 skill:发图片或面试题文本即可自动入库
5. 针对题库出考题检验理解(闪卡/追问/抽考/选择题)

## 2. 已确认的关键决策

| 决策点 | 结论 |
|---|---|
| 使用场景 | 只本地电脑,双击 index.html 离线使用;不部署 Pages;移动端不优先 |
| 笔记/熟练度存储 | 存进 Git 仓库(notes.json),页面自动读写该文件 |
| 考题模式 | 四种全要:闪卡自评、追问挑战、弱项抽考、选择题测验(MCQ) |
| skill 归属 | 项目级 .opencode/skill/,进仓库 |
| 架构 | 方案 A:content/ 每题一源文件 + build.py 生成 data.js + 单文件 SPA |

## 3. 总体架构(方案 A)

```
用户侧(双击即开)
  index.html(SPA 壳,手写,含 CSS/JS)──加载──> data.js(build 产物)
        │ 改动:笔记/熟练度 ──防抖3s──> notes.json(仓库文件,git 提交)
        └ localStorage 全程镜像(防丢 + 降级兜底)

内容生产侧
  content/questions/**/*.html(每题一源文件)
        │ python build.py(扫描/校验/生成)
        └> data.js
  .opencode/skill/add-question(发图/发题 ──> 写源文件 ──> build ──> commit)
```

- 产物只有 index.html + data.js 两个文件,file:// 直接可用(不用 fetch)
- data.js 通过 `<script src>` 加载,规避 file:// 的 CORS 限制
- 构建确定性排序,git diff 干净

## 4. 主题分类体系(8 主题,每题单一主归属)

| Code | 主题 | 覆盖内容 | 预计题数 | 主要来源 |
|---|---|---|---|---|
| rag | T1 RAG 检索增强生成 | 高并发RAG、检索优化、向量索引、混合检索、增量更新、多语言、流式RAG | ~12 | P1/P2 部分 |
| harness | T2 Agent 编排与 Harness | Agent定义组件、规划、工具调用、Multi-Agent、工作流引擎、状态持久化、并发调度 | ~25 | P5 大部分 + P1 部分 |
| ctx | T3 上下文工程与记忆 | Memory 系统、长对话压缩、个性化、上下文窗口管理 | ~6 | P1/P2/P4 部分 |
| infer | T4 模型层:推理部署与选型 | LLM Gateway、统一API、推理吞吐/vLLM/量化、LLM与Embedding选型 | ~10 | P1/P2/P3 部分 |
| eval | T5 评估、实验与可观测 | 评估平台、AB测试、灰度、监控、P7 评估与开放讨论 | ~24 | P1/P4/P7 |
| sys | T6 系统设计与高可用 | 分布式调度、语音多模态Agent、SSE流式、异常重试容错 | ~8 | P1/P2 |
| algo | T7 算法与训练原理 | 算法岗45题(RL/DPO/微调/Transformer)、FC微调数据生成 | ~46 | P6 + P1Q8 |
| biz | T8 安全、成本与业务落地 | 权限、隐私、幻觉、ROI、成本优化、框架选型、Prompt管理 | ~15 | P3/P4 |

规则:
- 每题一个主归属(topic 字段),原板块号存 `data-source`(如 P1Q1),不丢溯源
- 160 题 → 8 主题的精确映射表在 P1 实施第一步生成(标题+关键词自动映射 + 人工过一遍),
  生成后交用户确认再动手迁移
- 高频 Top10 速记保留为独立视图(#/top10),不占主题编号;其内容作为
  content/extra/top10.html 片段参与构建(内部链接改指新 qid)

## 5. 内容源格式规范

目录与命名:

```
content/
├─ topics.json                     # [{code,name,icon,order,desc}] × 8
├─ SPEC.md                         # 题卡写作规范(由 .fragments/SPEC.md 升级迁移)
├─ extra/
│  └─ top10.html                   # 考前速记片段
└─ questions/
   ├─ rag/001.html                 # qid = rag-001(目录 code + 三位序号)
   ├─ harness/001.html
   └─ algo/001.html
```

单个题目文件(完整格式):

```html
<article class="q" id="rag-001" data-topic="rag"
  data-title="设计一个支持日均 100 万查询的企业级 RAG 系统"
  data-difficulty="5" data-tags="系统设计,高并发,RAG"
  data-company="字节/阿里/腾讯(高频)" data-source="P1Q1"
  data-kw="RAG 高并发 缓存 检索 QPS P99 容量估算">
  <!-- 五模块题卡正文:30秒电梯稿/通俗类比/详细拆解/面试官追问/术语卡 -->
  ...(HTML 原样迁移,含表格/.arch/.code/SVG 架构图/details 追问)...
</article>
<script type="application/json">
[
  {"q":"题干","options":["A","B","C","D"],"answer":2,"explain":"解析文字"}
]
</script>
```

要点:
- article 标签属性即元数据载体,不发明新 front-matter 语法
- MCQ 紧跟题卡同文件,每题 2~3 道;存量题分批补写(见分期)
- 题卡 HTML 与现有 SPEC 质量标准一致,新增题一律按 content/SPEC.md 撰写

## 6. 构建系统 build.py

```
python build.py
  1. 扫描 content/questions/**/*.html,解析 article 属性 + 题卡 HTML + MCQ JSON
  2. 校验(任一失败即报错退出,错误信息含文件名与原因):
     - qid 唯一,且 id 属性与目录/文件名一致
     - topic ∈ topics.json
     - difficulty 为 1..5;title 非空
     - MCQ:options 恰 4 项、answer ∈ 0..3、explain 非空;无 MCQ 的题输出警告清单(不阻断)
  3. 生成 data.js:
     window.QB = {
       version, builtAt,
       topics: [...topics.json 排序后...],
       top10: "top10 片段 HTML(链接已重写)",
       questions: [{id,topic,title,kw,difficulty,tags,company,source,html,mcq}]
     }
  4. 打印构建摘要:各主题题数、MCQ 覆盖率、产物大小
python build.py --selftest   # 用内置坏样例断言校验逻辑
python build.py --migrate    # 一次性:从旧 index.html 拆出 160 题(配合映射表)
```

- 排序确定性:主题按 topics.json order,题按序号;UTF-8
- 原子写:先写 data.js.tmp 校验通过后替换,绝不产出半截文件
- 旧 .fragments/ 与 assemble.ps1 退役删除(本就是 gitignore 的脚手架)

## 7. SPA 页面结构

| 路由 | 视图 | 说明 |
|---|---|---|
| #/ | 仪表盘 | 总进度环(已掌握 x/160)、8 主题进度条、继续上次、练习入口、搜索 |
| #/topic/:code | 主题页 | 题目列表(难度+熟练度色点),按熟练度/难度过滤 |
| #/q/:qid | 题目页(核心) | 面包屑+同主题上下题切换;题卡全文;底部固定:笔记框+熟练度四档;键盘 ←/→ 切题、1-4 评档 |
| #/practice | 练习中心 | 四模式入口、熟练度分布、MCQ 覆盖率 |
| #/quiz/flash | 闪卡自评 | 正面=标题+提示句,展开完整题卡后自评 |
| #/quiz/grill | 追问挑战 | 抽 details 追问当考题,先想后看参考答案,自评 |
| #/quiz/drill | 弱项抽考 | 熟练度加权抽题组卷,闪卡式过卡,出薄弱清单 |
| #/quiz/mcq | 选择题测验 | 抽 MCQ 单选,即时判分+解析,成绩单+错题跳转 |
| #/notes | 笔记总览 | 全部笔记集中浏览、导出/导入、同步状态 |
| #/top10 | 考前速记 | Top10 片段渲染 |

通用交互:深浅色主题沿用(含 localStorage 记忆);桌面侧边导航,移动端汉堡;
题目页答案不折叠(追问保留 details)。

熟练度四档:`0 未学(默认) / 1 模糊 / 2 熟练 / 3 精通`。

## 8. 笔记/熟练度存储与 Git 同步

notes.json(仓库根目录,git 提交):

```json
{
  "version": 1,
  "updatedAt": "2026-11-13T12:00:00+08:00",
  "items": {
    "rag-001": {"level": 3, "note": "...", "updated": "...", "history": [{"t":"...","mode":"flash","level":2}]}
  }
}
```

读写机制(file:// 场景):
1. 主存储 = 仓库 notes.json;首次使用点"连接笔记文件"(showOpenFilePicker),
   句柄持久化到 IndexedDB,之后打开自动重连
2. 每次改动:立即写 localStorage(镜像+防丢)→ 防抖 3s 经句柄写回 notes.json;
   页面卸载前强制 flush
3. 加载优先级:文件句柄可用读文件;否则 localStorage;内容冲突以 updated 时间戳新者胜
4. 页面显示同步状态徽标:已同步 / 未同步 / 本地模式
5. 降级:不支持 File System Access API 的浏览器(如 Firefox)→ localStorage + 手动
   导出/导入 JSON 覆盖仓库文件
6. notes.json 解析失败:自动备份坏文件(notes.json.bak)→ 从 localStorage 恢复 → 提示

## 9. 四种考题模式(判定规则)

| 模式 | 出题来源 | 判定/写档规则 |
|---|---|---|
| 闪卡自评 | 全部题 | 自评直接更新该题主档 |
| 追问挑战 | 含 details 追问的题 | 自评只记 history,不改主档(单次追问不定级) |
| 弱项抽考 | 配置主题范围+题数,权重 未学:模糊:熟练:精通=10:5:2:1 | 闪卡式过卡+自评(更新主档);结束输出本次薄弱题清单 |
| 选择题测验 | 有 MCQ 的题,按配置抽 N 道 | 自动判分+解析;错题不自动降档,成绩单提供"一键标记为模糊" |

- 闪卡正面默认只显示题目标题/难度/公司/标签;另设可选"提示"按钮,点按后展示通俗
  类比的主题词(如"类比:医院分诊台"),辅助回忆但不含答案内容
- 练习中心实时显示各模式可出题量与 MCQ 覆盖率

## 10. add-question skill 规范

位置:`.opencode/skill/add-question/SKILL.md`(项目级,进仓库)

触发:在项目内发送题目截图/照片、粘贴面试题文本、或说"加题/入库/补充题库"。

流程:
1. 解析输入:图片→多模态读取提取题面;文本→直接使用
2. 查重:与现有题 title+kw 比对;高度相似→扩充模式(为已有题追加追问/MCQ),不新建
3. 归类:映射到 8 主题;置信度低→列出候选主题请用户选
4. 写题卡:遵循 content/SPEC.md,五模块结构,质量对齐存量题
5. 写 MCQ:2~3 道+解析
6. 跑 python build.py,校验必须通过
7. git commit(只 commit 不 push);回复新题链接(#/q/qid)与摘要

边界:支持一次多张图片批量入库;图片模糊/信息不全→询问用户;来源(公司/帖子)已知时
写入 data-company。

## 11. 实施分期与验收

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| P1 地基 | 映射表生成并经用户确认 → build.py(--migrate/--selftest) → content/ 结构落地 → SPA 壳(仪表盘/主题页/题目页/笔记/熟练度/notes.json 同步) | 双击可用;160 题内容+SVG 零丢失(脚本比对拆分前后标题与正文) |
| P2 练习 | 四种考题模式 + #/notes + #/top10 | 三模式可用;MCQ 模式可运行并显示覆盖率 |
| P3 内容+skill | MCQ 补写(rag→harness→infer→其余,每批 15~25 题×2~3 道,批间 build 验收);add-question skill 上线 | MCQ 覆盖率逐步达 100%;skill 跑通 1 图+1 文两个真实样例 |
| P4 收尾 | README 重写(用法/构建/加题指南)、.fragments 清理、旧长文版说明(git 历史保留) | 仓库自解释,他人克隆后可 build、可加题 |

## 12. 错误处理汇总

- build.py:校验失败拒绝生成,错误带文件名+原因;原子写;--selftest 内置坏样例回归
- data.js 缺失/损坏:SPA 显示明确错误页(提示重新 build),不白屏
- notes.json 损坏:备份+从 localStorage 恢复+提示;句柄失效(文件被移动/重命名):引导重连
- FS Access API 不可用:自动进入本地模式,导出/导入兜底
- skill:查重命中走扩充;分类存疑必问;build 不过不许提交

## 13. 测试与验证策略

- build.py 单测:--selftest 覆盖 id 冲突/非法 topic/坏 MCQ/原子写
- 迁移验证:migrate 后脚本比对 160 题标题清单与正文长度,零丢失才继续
- 端到端冒烟(每阶段验收时):浏览器自动化走通——打开/搜索/切题/评档/写笔记并检查
  notes.json 落盘/四模式各一轮/深浅色切换
- skill 验收:一张真实题目截图 + 一段题面文本,各走通全流程到 commit

## 14. 不做的事(YAGNI)

- 不做多用户/云端同步/账号
- 不部署 GitHub Pages、不做手机优先适配(现有响应式布局顺带保留)
- 不做 SM-2 间隔重复算法(以"熟练度加权抽考"替代,复杂度收益比不佳)
- 题目不做多主题多归属(单一主 topic + kw 标签已够检索过滤)
- 不引入 Node 工具链/前端构建框架
