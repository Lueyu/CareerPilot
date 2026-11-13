# AI Agent 面试题库

面向 **AI 应用开发 / Agent 开发 / RAG 系统工程师 / Agent 算法岗**社招的中文面试题库,共 **160 题**,按真实考察主题分为 8 大类。纯静态、零依赖、离线可用——`index.html` 双击即开。

## 主题分布

| 主题 | 题数 | 覆盖 |
|---|---|---|
| 🔍 RAG 检索增强生成 | 12 | 高并发架构、检索优化、混合检索、增量更新、多语言、GraphRAG |
| 🤖 Agent 编排与 Harness | 32 | Agent 定义、规划、工具调用、Multi-Agent、工作流引擎、反思修正 |
| 🧠 上下文工程与记忆 | 8 | Memory 系统、长对话压缩、记忆衰退、个性化 |
| ⚙️ 模型推理与部署 | 7 | LLM Gateway、统一 API、vLLM、量化、选型 |
| 📊 评估与可观测 | 30 | 评估平台、实验设计、LLM-as-judge、红队、灰度 |
| 🏗️ 系统设计与高可用 | 10 | 分布式调度、多租户、语音多模态、SSE、容错 |
| 🧮 算法与训练原理 | 37 | RLHF/DPO/PPO、微调蒸馏、Transformer 推导、MoE |
| 💼 选型、安全与业务落地 | 24 | 框架选型、权限隐私、幻觉治理、ROI |

每题按「30秒电梯稿 → 通俗类比 → 详细拆解 → 面试官追问(可折叠) → 术语卡」五层展开,零基础也能看懂,面试前可直接背。

## 快速开始

```bash
git clone https://github.com/Lueyu/agent-interview.git
cd agent-interview
```

**直接刷题**:双击 `index.html`(数据已随仓库附带,无需任何环境)。

**记笔记/标熟练度**(推荐):首次使用执行一次

```bash
python build.py --init-notes   # 在仓库根目录创建 notes.json(个人数据,已 gitignore)
```

然后浏览器打开 `index.html` → 点顶栏「连接笔记」→ 选择刚创建的 `notes.json` → 授权(建议勾选"每次访问都允许")。之后你的笔记、四档熟练度(未学/模糊/熟练/精通)会自动保存,跨标签页自动合并。

快捷键:题目页 `←`/`→` 切题,`1-4` 评熟练度;顶栏可全局搜索;右上角切换深浅色。

## 如何贡献新题目

欢迎把你遇到的真题贡献进来(附答案)。两种方式:

### 方式 A:用 opencode(推荐)

1. Fork 并克隆本仓库,在仓库目录里启动 [opencode](https://opencode.ai)
2. 把题目**截图/照片**或**文本**发给它,或直接说"加题/入库"
3. 本仓库内置的 `add-question` skill 会自动:识别题目 → 查重 → 归入 8 大主题 → 按 [content/SPEC.md](content/SPEC.md) 写成与现有题库同款的五模块题卡 + 2~3 道选择题(附解析)→ 运行 `python build.py` 校验 → git commit
4. 你确认后 push 到你的 fork,向本仓库提 Pull Request

### 方式 B:任意工具(手动)

1. 阅读 [content/SPEC.md](content/SPEC.md)(题目写作规范,含完整模板)
2. 新建 `content/questions/{主题code}/{下一个三位序号}.html`,按模板写题卡 + MCQ JSON
3. 运行 `python build.py`——校验通过(0 error)后 `data.js` 会自动重新生成
4. 提交 `content/` 与 `data.js` 的变更,发 Pull Request

### 贡献须知

- **PR 只包含题目内容**:`content/questions/**`、`data.js`(如重跑过构建)。校验报错会列出文件名和原因,修复后再提交
- **绝不提交个人数据**:`notes.json`(笔记/熟练度)、`notes-export-*.json` 已被 `.gitignore` 忽略,任何时候都不要把它们加进提交(见下方隐私说明)
- 题目来源请注明公司(如"字节·真题"),不确定的写"网友面经"
- 技术准确性最高优先级:公式写错比不写更糟,不确定的细节用保守表述

### 修改/升级已有答案

觉得某道题的答案不够好?把别的 AI 给的更满意回答交进来:

- **单题**:在 opencode 里粘贴,说"美化 rag-003 的答案:[内容]"
- **批量**:存到 `drafts/` 目录(文件名带题号,如 `drafts/rag-003.txt`),说"处理草稿"

内置的 `refine-answer` skill 会全量重写题卡(五模块+选择题,流程性内容自动生成 SVG 架构图
/表格),元数据保持不变,`python build.py` 校验通过后提交。旧版永远在 git 历史里,
`git revert` 即可回退。草稿目录与个人笔记一样不进仓库。

## 隐私说明

你的**笔记、熟练度、复习进度**只保存在本地 `notes.json`(由浏览器直写,已 gitignore),换设备用页面里的「导出备份/导入备份」迁移。**这些个人数据永远不会进入 Git 仓库**——贡献题目时也只会上传题库内容本身。

## 项目结构

```
index.html                       # v2 单页应用(双击即开)
data.js                          # 构建产物:window.QB(题目数据)
notes.json                       # 你的个人笔记(本地生成,不入库)
drafts/                          # 答案草稿箱(本地,不入库;refine-answer skill 消化)
build.py                         # 构建工具:校验/生成/自测/迁移/初始化笔记
content/
├─ topics.json                   # 8 主题配置
├─ SPEC.md                       # 题目写作规范(贡献必读)
├─ extra/top10.html              # 考前速记 Top10
└─ questions/{topic}/{NNN}.html  # 每题一文件(题卡+元数据+MCQ)
.opencode/skill/                 # opencode skills:add-question(加题)/ refine-answer(美化答案)
docs/superpowers/                # 设计文档与实施计划
```

## 维护命令

```bash
python build.py                # 扫描 content/ → 校验 → 生成 data.js
python build.py --selftest     # 内置回归(57 项断言)
python build.py --init-notes   # 创建本地笔记文件(新环境首次使用)
python build.py --migrate      # (已弃用)旧版单文件 index.html 一次性迁移,输出树已存在时会拒绝执行
```

修改题目源文件后重新运行 `python build.py` 并刷新页面即可生效。
