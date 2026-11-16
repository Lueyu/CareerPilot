#!/usr/bin/env node
// portal/build.mjs —— agent-interview.icu 公共知识门户 · 零依赖静态构建
// 用法: node portal/build.mjs
// 输入: portal/content/**/*.md (frontmatter + markdown)
//       web/src/data/bank.json (公开面试题子集)
// 输出: portal/dist/ (index.html / 分类列表 / 文章页 / questions / search.json / sitemap.xml / robots.txt)
import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync, existsSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const CONTENT = join(__dirname, 'content');
const DIST = join(__dirname, 'dist');
const BANK = join(ROOT, 'web', 'src', 'data', 'bank.json');
const SITE = 'https://agent-interview.icu';

/* ---------- frontmatter + markdown ---------- */
function parseFrontmatter(raw) {
  if (!raw.startsWith('---')) return { meta: {}, body: raw };
  const end = raw.indexOf('\n---', 3);
  if (end < 0) return { meta: {}, body: raw };
  const head = raw.slice(3, end).trim();
  const body = raw.slice(raw.indexOf('\n', end + 1) + 1);
  const meta = {};
  for (const line of head.split('\n')) {
    const m = line.match(/^(\w[\w_]*):\s*(.*)$/);
    if (!m) continue;
    let v = m[2].trim();
    if (v.startsWith('[') && v.endsWith(']')) v = v.slice(1, -1).split(',').map((s) => s.trim().replace(/^['"]|['"]$/g, ''));
    meta[m[1]] = v;
  }
  return { meta, body };
}

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/\*([^*]+)\*/g, '<i>$1</i>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
}

function renderMd(md) {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;
  let inUl = false, inOl = false;
  const closeLists = () => { if (inUl) { out.push('</ul>'); inUl = false; } if (inOl) { out.push('</ol>'); inOl = false; } };
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith('```')) {
      closeLists();
      const lang = line.slice(3).trim();
      const buf = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith('```')) { buf.push(lines[i]); i += 1; }
      i += 1;
      out.push(`<pre data-lang="${esc(lang)}"><code>${esc(buf.join('\n'))}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { closeLists(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i += 1; continue; }
    if (/^(-{3,}|\*{3,})$/.test(line.trim())) { closeLists(); out.push('<hr>'); i += 1; continue; }
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ul) { if (!inUl) { closeLists(); out.push('<ul>'); inUl = true; } out.push(`<li>${inline(ul[1])}</li>`); i += 1; continue; }
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) { if (!inOl) { closeLists(); out.push('<ol>'); inOl = true; } out.push(`<li>${inline(ol[1])}</li>`); i += 1; continue; }
    if (line.startsWith('>')) { closeLists(); out.push(`<blockquote>${inline(line.replace(/^>\s?/, ''))}</blockquote>`); i += 1; continue; }
    if (!line.trim()) { closeLists(); i += 1; continue; }
    // 段落: 连续非空行合并
    const buf = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*[-*]\s|\s*\d+\.\s|>)/.test(lines[i])) { buf.push(lines[i]); i += 1; }
    out.push(`<p>${inline(buf.join(' '))}</p>`);
  }
  closeLists();
  return out.join('\n');
}

/* ---------- 布局 ---------- */
const NAV = [
  { href: '/', label: '首页' },
  { href: '/paths/', label: '学习路线' },
  { href: '/guides/', label: '技术知识库' },
  { href: '/questions/', label: '面试题库' },
  { href: '/labs/', label: '实践实验室' },
  { href: '/about/', label: '关于' },
];

const LEVEL_NAME = { beginner: '入门', intermediate: '进阶', advanced: '高级' };
function transformAnswer(html) {
  return html.replace(/<p><b>(\d+)\. ?([^<]+)<\/b><\/p>/g, '<div class="qa-h"><span class="qa-no">$1</span><span class="qa-t">$2</span></div>');
}
const SD_MARKER_DEFS = '<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs><marker id="sd-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#5b5bd6"/></marker></defs></svg>';
const CAT_NAME = { paths: '学习路线', guides: '技术知识库', questions: '面试题库', labs: '实践实验室', cases: '工程案例', changelog: '更新日志', about: '关于' };

function layout({ title, desc, body, activeNav = '', extraHead = '' }) {
  return `<!doctype html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)} · agent-interview.icu</title>
<meta name="description" content="${esc(desc || '从理解 Agent，到构建、评估和交付可靠的 Agent 系统。')}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
${extraHead}
<style>
:root{--bg:#0f1218;--card:#161a22;--surface:#1d222c;--border:#2b323e;--text:#e7eaf0;--text2:#b9c1cd;--text3:#939dac;--primary:#8b8bf5;--primary50:#1c1e36;--green:#4ade80;--amber:#fbbf24;--radius:12px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.8 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
a{color:var(--primary);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1080px;margin:0 auto;padding:0 24px}
header.site{position:sticky;top:0;z-index:9;background:rgba(15,18,24,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--border)}
header.site .wrap{display:flex;align-items:center;gap:22px;height:60px}
.brand{display:flex;align-items:center;gap:10px;color:var(--text);font-weight:700;font-size:16px}
.brand:hover{text-decoration:none}
.brand .logo{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,#6d5df6,#8b5cf6);display:flex;align-items:center;justify-content:center;font-size:13px;color:#fff}
.brand small{display:block;font-size:9.5px;letter-spacing:.18em;color:var(--text3);font-weight:500}
nav.main{display:flex;gap:4px;flex:1;flex-wrap:wrap}
nav.main a{color:var(--text2);padding:6px 12px;border-radius:8px;font-size:14px}
nav.main a:hover{color:var(--text);background:var(--surface);text-decoration:none}
nav.main a.on{color:var(--primary);background:var(--primary50)}
.hero{padding:72px 0 56px;text-align:center;background:radial-gradient(700px 300px at 50% -40px,rgba(109,93,246,.18),transparent)}
.hero h1{font-size:34px;margin:0 0 14px;line-height:1.35}
.hero h1 .grad{background:linear-gradient(120deg,#a5a5fa,#8b5cf6);-webkit-background-clip:text;background-clip:text;color:transparent}
.hero p{color:var(--text2);font-size:16px;max-width:640px;margin:0 auto 26px}
.cta{display:inline-flex;gap:12px;justify-content:center}
.btn{display:inline-block;padding:11px 22px;border-radius:10px;font-size:14.5px}
.btn.primary{background:linear-gradient(135deg,#6d5df6,#8b5cf6);color:#fff;font-weight:600}
.btn.ghost{border:1px solid var(--border);color:var(--text2)}
.btn:hover{text-decoration:none;filter:brightness(1.08)}
section.block{padding:34px 0}
section.block h2{font-size:22px;margin:0 0 18px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:20px 22px;transition:border-color .15s}
.card:hover{border-color:var(--primary)}
.card h3{margin:0 0 8px;font-size:16.5px}
.card h3 a{color:var(--text)}
.card p{margin:0;color:var(--text2);font-size:13.5px}
.meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.tag{font-size:11.5px;color:var(--text3);border:1px solid var(--border);border-radius:99px;padding:2px 10px}
.tag.lv{color:var(--green);border-color:rgba(74,222,128,.35)}
.topic-cloud{display:flex;gap:10px;flex-wrap:wrap}
.topic-cloud a{border:1px solid var(--border);border-radius:99px;padding:7px 18px;color:var(--text2);font-size:14px}
.topic-cloud a:hover{color:var(--primary);border-color:var(--primary);text-decoration:none}
ul.updates{list-style:none;margin:0;padding:0}
ul.updates li{display:flex;gap:14px;padding:10px 0;border-bottom:1px solid var(--border);font-size:14px}
ul.updates time{color:var(--text3);flex:none;font-size:13px}
.cp-cta{background:linear-gradient(135deg,#141a2e,#1c1e36);border:1px solid var(--border);border-radius:14px;padding:34px;text-align:center}
footer.site{border-top:1px solid var(--border);padding:28px 0;color:var(--text3);font-size:13px}
footer.site .wrap{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
/* 文章页 */
article.page{display:grid;grid-template-columns:220px 1fr;gap:34px;padding:36px 0 60px;align-items:start}
aside.toc{position:sticky;top:80px;font-size:13px}
aside.toc .t{font-weight:600;color:var(--text3);letter-spacing:.06em;margin-bottom:8px;font-size:12px}
aside.toc a{display:block;color:var(--text2);padding:4px 0;border-left:2px solid transparent;padding-left:10px}
aside.toc a:hover{color:var(--primary);text-decoration:none}
article .doc h1{font-size:28px;margin:0 0 10px}
article .doc .pmeta{color:var(--text3);font-size:13px;margin-bottom:26px;padding-bottom:14px;border-bottom:1px solid var(--border)}
article .doc h2{font-size:20px;margin-top:34px}
article .doc h3{font-size:16.5px;margin-top:24px}
article .doc code{background:var(--surface);border-radius:5px;padding:1px 6px;font-size:.9em}
article .doc pre{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;overflow-x:auto}
article .doc pre code{background:none;padding:0}
article .doc blockquote{border-left:3px solid var(--primary);margin:14px 0;padding:6px 16px;color:var(--text2);background:var(--primary50);border-radius:0 8px 8px 0}
.list-head{padding:40px 0 8px}
.list-head h1{margin:0 0 6px;font-size:26px}
.list-head p{color:var(--text2);margin:0}
.q-item{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:16px 20px;margin-bottom:12px}
.q-item h3{margin:0 0 6px;font-size:15.5px}
.q-item h3 a{color:var(--text)}
.q-item p{margin:0;color:var(--text2);font-size:13px}
/* 面试题详解(bank html) */
/* 架构图(sd-* SVG): 亮底保证可见 */
.bank-answer .sdiagram{display:block;width:100%;height:auto;max-width:920px;margin:14px 0;background:#fff;border:1px solid #e2e6f2;border-radius:12px;padding:10px 6px;box-sizing:border-box}
.bank-answer .sd-layer{fill:#eef1f8;stroke:#d6dbec;stroke-width:1.2}
.bank-answer .sd-l{fill:#44506e;font-size:15px;font-weight:700}
.bank-answer .sd-box,.bank-answer .sd-box2{fill:#fff;stroke:#b9c2dd;stroke-width:1.3}
.bank-answer .sd-box-amber{fill:#fdf3e2;stroke:#e4b45c;stroke-width:1.3}
.bank-answer .sd-box-rose{fill:#fdeef0;stroke:#e79aa4;stroke-width:1.3}
.bank-answer .sd-box-teal{fill:#e6f6f3;stroke:#6fc3b4;stroke-width:1.3}
.bank-answer .sd-t{fill:#1f2937;font-size:13px;font-weight:600}
.bank-answer .sd-ts{fill:#6b7280;font-size:10.5px}
.bank-answer .sd-a{fill:none;stroke:#5b5bd6;stroke-width:1.6}
.bank-answer .sd-an{fill:none;stroke:#9aa3bd;stroke-width:1.2;stroke-dasharray:4 3}
.bank-answer h3{font-size:16.5px;margin:24px 0 8px;color:var(--text)}
.bank-answer p{color:var(--text2);margin:8px 0}
.bank-answer ul,.bank-answer ol{color:var(--text2);padding-left:22px;margin:8px 0}
.bank-answer code{background:var(--surface);border-radius:5px;padding:1px 6px;font-size:.9em}
.bank-answer pre{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px;overflow-x:auto}
.bank-answer pre code{background:none;padding:0}
.bank-answer blockquote{border-left:3px solid var(--primary);margin:10px 0;padding:6px 14px;color:var(--text2);background:var(--primary50);border-radius:0 8px 8px 0}
.bank-answer table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13.5px}
.bank-answer th{background:var(--surface);color:var(--text3);text-align:left;font-weight:600}
.bank-answer th,.bank-answer td{border:1px solid var(--border);padding:8px 10px}
/* 静态题库 html 内部结构(见 web/src/styles/components.css 同名类) */
.bank-answer .q-no{font-size:12px;letter-spacing:.08em;color:var(--text3);margin-bottom:10px}
.bank-answer .q-meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0 18px;font-size:12.5px;color:var(--text3)}
.bank-answer .tag{display:inline-block;background:var(--surface);border:1px solid var(--border);border-radius:20px;padding:2px 10px;font-size:12px;color:var(--text2)}
.bank-answer .company{color:var(--text2);font-weight:600}
.bank-answer .note{background:var(--primary50);border:1px solid var(--border);border-left:3px solid var(--primary);border-radius:0 10px 10px 0;padding:12px 16px;margin:14px 0;color:var(--text2)}
.bank-answer .block{border:1px solid var(--border);border-radius:12px;padding:16px 18px;margin:16px 0;background:var(--card)}
.bank-answer .badge{display:inline-block;font-size:12px;font-weight:700;border-radius:6px;padding:3px 10px;margin-bottom:10px}
.bank-answer .b-fast{background:#3a2d10;color:var(--amber)}
.bank-answer .b-ana{background:#12293d;color:var(--blue)}
.bank-answer .b-det{background:#12301f;color:var(--green)}
.bank-answer .b-fol{background:#201f3d;color:var(--purple)}
.bank-answer .b-term{background:#222835;color:var(--text2)}
.bank-answer .gl-term{border-bottom:1px dashed var(--text3)}
.bank-answer details.qa{border:1px solid var(--border);border-radius:10px;margin:10px 0;background:var(--surface)}
.bank-answer details.qa summary.qu{cursor:pointer;padding:11px 14px;font-weight:600;color:var(--text);list-style:none}
.bank-answer details.qa summary.qu::-webkit-details-marker{display:none}
.bank-answer details.qa[open] summary.qu{border-bottom:1px solid var(--border)}
.bank-answer details.qa .an{padding:12px 14px}
.bank-answer .terms-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px}
.bank-answer .term-item{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 12px;font-size:13.5px;color:var(--text2)}
.bank-answer .term-item b{display:block;color:var(--text);margin-bottom:3px}
/* 可读性 v2: 章节标题/追问折叠箭头/图表居中 */
.bank-answer .qa-h{display:flex;align-items:baseline;gap:10px;margin:26px 0 12px;padding:10px 14px 10px 12px;background:var(--surface);border-left:3px solid var(--primary);border-radius:0 10px 10px 0;font-size:15.5px;font-weight:700;color:var(--text)}
.bank-answer .qa-no{flex-shrink:0;font-size:12px;font-weight:800;color:#fff;background:var(--primary);border-radius:6px;padding:2px 8px}
.bank-answer tbody tr:nth-child(even),.bank-answer tr:nth-child(even){background:var(--surface)}
.bank-answer details.qa{position:relative}
.bank-answer details.qa summary.qu{position:static;padding-left:34px}
.bank-answer details.qa summary.qu::before{content:'▸';position:absolute;left:14px;color:var(--primary);transition:transform .15s ease}
.bank-answer details.qa[open] summary.qu::before{transform:rotate(90deg)}
.bank-answer .sdiagram{margin:16px auto;max-width:880px}
@media (max-width:900px){.grid3{grid-template-columns:1fr}.grid2{grid-template-columns:1fr}article.page{grid-template-columns:1fr}aside.toc{display:none}nav.main a{padding:6px 8px;font-size:13px}}
</style>
</head>
<body>
<header class="site"><div class="wrap">
  <a class="brand" href="/"><span class="logo">CP</span><span>agent-interview.icu<small>AGENT ENGINEERING</small></span></a>
  <nav class="main">${NAV.map((n) => `<a href="${n.href}" ${activeNav === n.href ? 'class="on"' : ''}>${n.label}</a>`).join('')}</nav>
</div></header>
${body}
<footer class="site"><div class="wrap">
  <span>© 2026 agent-interview.icu · 公开知识门户</span>
  <span>内容许可 CC BY-NC-SA 4.0 · <a href="/about/">关于本站</a> · Powered by CareerPilot</span>
</div></footer>
</body>
</html>`;
}

function docPage({ meta, html, cat }) {
  const toc = (html.match(/<h2>(.*?)<\/h2>/g) || []).map((h) => {
    const t = h.replace(/<\/?h2>/g, '');
    return `<a href="#${slug(t)}">${t}</a>`;
  }).join('');
  const body = `<div class="wrap"><article class="page">
  ${toc ? `<aside class="toc"><div class="t">本页目录</div>${toc}</aside>` : ''}
  <div class="doc">
    <h1>${esc(meta.title)}</h1>
    <div class="pmeta">${CAT_NAME[cat] || cat} · ${LEVEL_NAME[meta.level] || meta.level || ''} · 约 ${meta.estimated_reading_minutes || 10} 分钟 · 更新 ${esc(meta.updated_at || meta.published_at || '')}${meta.tags ? ' · ' + (Array.isArray(meta.tags) ? meta.tags : [meta.tags]).map((t) => esc(t)).join(' / ') : ''}</div>
    ${html}
  </div>
</article></div>`;
  return layout({ title: meta.title, desc: meta.description, body, activeNav: `/${cat}/` });
}

const slug = (s) => esc(s).toLowerCase().replace(/[^\w\u4e00-\u9fa5]+/g, '-');

function write(rel, content) {
  const p = join(DIST, rel);
  mkdirSync(dirname(p), { recursive: true });
  writeFileSync(p, content, 'utf8');
}

/* ---------- 收集内容 ---------- */
function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir).sort()) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (name.endsWith('.md')) out.push(p);
  }
  return out;
}

const docs = walk(CONTENT).map((p) => {
  const { meta, body } = parseFrontmatter(readFileSync(p, 'utf8'));
  const cat = meta.category || relative(CONTENT, dirname(p)).split(/[\\/]/)[0];
  return { path: p, meta: { ...meta, category: cat }, html: renderMd(body) };
});

/* 文章页 + h2 锚点 */
for (const d of docs) {
  const rel = relative(CONTENT, d.path).replace(/\.md$/, '').replace(/\\/g, '/');
  d.url = `/${rel}/`;
  d.html = d.html.replace(/<h2>(.*?)<\/h2>/g, (m, t) => `<h2 id="${slug(t.replace(/<[^>]+>/g, ''))}">${t}</h2>`);
}

/* ---------- 面试题库(来自公开静态库的子集) ---------- */
const questions = [];
if (existsSync(BANK)) {
  const bank = JSON.parse(readFileSync(BANK, 'utf8'));
  const seen = new Set();
  for (const q of bank.questions) {
    if (questions.length >= 20) break;
    const key = q.title.slice(0, 18);
    if (seen.has(key)) continue;
    seen.add(key);
    questions.push(q);
  }
}
const stripHtml = (s) => String(s).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();

/* ---------- 首页 ---------- */
const guides = docs.filter((d) => d.meta.category === 'guides');
const paths = docs.filter((d) => d.meta.category === 'paths');
const labs = docs.filter((d) => d.meta.category === 'labs');
const featured = [...guides].sort((a, b) => String(b.meta.published_at).localeCompare(String(a.meta.published_at))).slice(0, 6);
const topics = ['Memory', 'Tool Calling', 'RAG', 'Evaluation', 'Reliability', 'MCP'];
const updates = [...docs].sort((a, b) => String(b.meta.published_at).localeCompare(String(a.meta.published_at))).slice(0, 8);

const indexBody = `<div class="hero"><div class="wrap">
  <h1>从理解 Agent，到<span class="grad">构建、评估和交付</span>可靠的 Agent 系统</h1>
  <p>面向 AI Agent 应用工程师的公开学习资料：学习路线、深度技术文章、真实面试题与实践实验室。全部内容免费公开。</p>
  <div class="cta">
    <a class="btn primary" href="/paths/ai-agent-engineer-0-1/">开始学习路线</a>
    <a class="btn ghost" href="/guides/">浏览知识库</a>
  </div>
</div></div>
<div class="wrap">
<section class="block">
  <div class="grid3">
    <a class="card" href="/paths/"><h3>🗺 学习路线</h3><p>AI Agent 应用开发工程师 0→1 成长路线：八个能力域的阶段目标与学习顺序。</p></a>
    <a class="card" href="/guides/"><h3>📚 技术知识库</h3><p>工具调用、记忆、RAG、评估、可靠性……每篇都是「先讲人话，再讲原理」。</p></a>
    <a class="card" href="/questions/"><h3>📝 面试题库</h3><p>公开授权的 ${questions.length}+ 道真实面试题，附结构化详解与评分要点。</p></a>
  </div>
</section>
<section class="block">
  <h2>精选教程</h2>
  <div class="grid3">
    ${featured.map((d) => `<a class="card" href="${d.url}"><h3>${esc(d.meta.title)}</h3><p>${esc(d.meta.description || '')}</p><div class="meta"><span class="tag lv">${LEVEL_NAME[d.meta.level] || ''}</span><span class="tag">${esc(d.meta.estimated_reading_minutes || 10)} 分钟</span></div></a>`).join('')}
  </div>
</section>
<section class="block">
  <h2>热门主题</h2>
  <div class="topic-cloud">${topics.map((t) => `<a href="/guides/">${t}</a>`).join('')}</div>
</section>
<section class="block">
  <h2>最新更新</h2>
  <ul class="updates">
    ${updates.map((d) => `<li><time>${esc(String(d.meta.published_at || ''))}</time><a href="${d.url}">${esc(d.meta.title)}</a><span style="color:var(--text3)">· ${CAT_NAME[d.meta.category] || d.meta.category}</span></li>`).join('')}
  </ul>
</section>
<section class="block">
  <div class="cp-cta">
    <h2 style="margin:0 0 10px">想要一台自己的「职业副驾」？</h2>
    <p style="color:var(--text2);max-width:560px;margin:0 auto 20px">CareerPilot 是本地运行的私人职业工作台：导入你的面经与项目，它用证据评估你的能力差距，只让你学真正缺的东西。本站内容就是由它的学习引擎沉淀而来。</p>
    <a class="btn primary" href="/about/">了解 CareerPilot</a>
  </div>
</section>
</div>`;
write('index.html', layout({ title: '首页', desc: '面向 AI Agent 应用工程师的公开知识门户', body: indexBody, activeNav: '/' }));

/* ---------- 分类列表页 ---------- */
for (const cat of ['paths', 'guides', 'labs']) {
  const items = docs.filter((d) => d.meta.category === cat);
  const body = `<div class="wrap"><div class="list-head"><h1>${CAT_NAME[cat]}</h1><p>${cat === 'paths' ? '从零到一的成长路线，按能力域组织。' : cat === 'guides' ? '每篇文章 8 段结构：一句话结论、生活类比、具体例子、原理术语、常见误区、实践步骤、自测问题、延伸阅读。' : '动手实验：边做边学，完成即产出可复用的工程组件。'}</p></div>
  <section class="block">
    ${items.map((d) => `<a class="card" style="display:block;margin-bottom:14px" href="${d.url}"><h3>${esc(d.meta.title)}</h3><p>${esc(d.meta.description || '')}</p><div class="meta"><span class="tag lv">${LEVEL_NAME[d.meta.level] || ''}</span><span class="tag">${esc(d.meta.estimated_reading_minutes || 10)} 分钟</span>${(Array.isArray(d.meta.tags) ? d.meta.tags : []).map((t) => `<span class="tag">#${esc(t)}</span>`).join('')}</div></a>`).join('')}
  </section></div>`;
  write(`${cat}/index.html`, layout({ title: CAT_NAME[cat], desc: `${CAT_NAME[cat]} · agent-interview.icu`, body, activeNav: `/${cat}/` }));
}

/* ---------- 文章页 ---------- */
for (const d of docs) {
  write(`${relative(CONTENT, d.path).replace(/\.md$/, '').replace(/\\/g, '/')}/index.html`, docPage({ meta: d.meta, html: d.html, cat: d.meta.category }));
}

/* ---------- 面试题库页 ---------- */
{
  const TOPIC_NAME = { rag: 'RAG/上下文工程', harness: '工具调用/Agent框架', ctx: '状态与记忆', infer: '推理与后端', eval: 'Agent评估', sys: '系统与可靠性', algo: '算法与基础', biz: '业务与场景' };
  const list = questions.map((q, i) => {
    const sum = stripHtml(q.html).slice(0, 110);
    return `<div class="q-item"><h3><a href="/questions/q-${i + 1}/">${i + 1}. ${esc(q.title)}</a></h3><p>${esc(sum)}…</p><div class="meta"><span class="tag">${TOPIC_NAME[q.topic] || q.topic}</span><span class="tag">${'★'.repeat(Math.min(5, q.difficulty || 3))}</span>${q.company ? `<span class="tag">${esc(q.company)}</span>` : ''}</div></div>`;
  }).join('');
  const body = `<div class="wrap"><div class="list-head"><h1>面试题库</h1><p>公开授权子集（${questions.length} 题，节选自 161 题静态库）· 每题附完整结构化详解。私有题目不会出现在本页。</p></div>
  <section class="block">${list}</section></div>`;
  write('questions/index.html', layout({ title: '面试题库', desc: '公开授权的 Agent 工程师面试题与详解', body, activeNav: '/questions/' }));

  questions.forEach((q, i) => {
    const body = `<div class="wrap"><article class="page"><div class="doc">
      <h1>${esc(q.title)}</h1>
      <div class="pmeta">面试题库 · ${TOPIC_NAME[q.topic] || q.topic} · ${'★'.repeat(Math.min(5, q.difficulty || 3))}${q.company ? ' · ' + esc(q.company) : ''}</div>
      <div class="bank-answer">${SD_MARKER_DEFS}${transformAnswer(q.html)}</div>
      <p style="margin-top:26px"><a href="/questions/">← 返回题库列表</a></p>
    </div></article></div>`;
    write(`questions/q-${i + 1}/index.html`, layout({ title: q.title, desc: stripHtml(q.html).slice(0, 80), body, activeNav: '/questions/' }));
  });
}

/* ---------- 搜索索引 / sitemap / robots ---------- */
const searchIndex = [
  ...docs.map((d) => ({ t: d.meta.title, d: d.meta.description || '', u: d.url, c: d.meta.category })),
  ...questions.map((q, i) => ({ t: q.title, d: stripHtml(q.html).slice(0, 80), u: `/questions/q-${i + 1}/`, c: 'questions' })),
];
write('search.json', JSON.stringify(searchIndex), 'utf8');

const urls = ['/', ...docs.map((d) => d.url), '/questions/', ...questions.map((q, i) => `/questions/q-${i + 1}/`)];
write('sitemap.xml', `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls.map((u) => `  <url><loc>${SITE}${u}</loc></url>`).join('\n')}\n</urlset>\n`);
write('robots.txt', `User-agent: *\nAllow: /\nSitemap: ${SITE}/sitemap.xml\n`);

console.log(`[portal] ${docs.length} 篇文章 · ${questions.length} 道公开题 → ${DIST}`);
