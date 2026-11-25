#!/usr/bin/env node
// portal/build.mjs —— agent-interview.icu 公共知识门户 · 零依赖静态构建
// 用法: node portal/build.mjs
// 输入: portal/content/**/*.md (frontmatter + markdown)
//       web/src/data/bank.json (公开面试题子集)
// 输出: portal/dist/ (简体中文 / 英文静态页面、搜索索引、网站地图)
import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync, existsSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const CONTENT = join(__dirname, 'content');
const DIST = process.env.PORTAL_DIST || join(__dirname, 'dist');
const BANK = join(ROOT, 'web', 'src', 'data', 'bank.json');
const SITE = 'https://agent-interview.icu';
const LANGUAGES = ['zh-CN', 'en'];
const localePath = (language, page) => language === 'en' ? '/en' + page : page;
const fill = (message, values) => message.replace(/\{(\w+)\}/g, (token, key) => values[key] ?? token);

function loadLocale(language) {
  const copy = JSON.parse(readFileSync(join(__dirname, 'locales', language + '.json'), 'utf8'));
  const formatter = new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeZone: 'UTC' });
  const number = new Intl.NumberFormat(language);
  const date = value => {
    if (language === 'zh-CN') return value;
    const timestamp = Date.parse(value + 'T00:00:00Z');
    return Number.isNaN(timestamp) ? value : formatter.format(timestamp);
  };
  return {
    language,
    copy,
    url: page => localePath(language, page),
    number: value => number.format(value),
    date,
  };
}

/* ---------- frontmatter + markdown ---------- */
function parseFrontmatter(raw) {
  if (!raw.startsWith('---')) return { meta: {}, body: raw };
  const end = raw.indexOf('\n---', 3);
  if (end < 0) return { meta: {}, body: raw };
  const head = raw.slice(3, end).trim();
  const body = raw.slice(raw.indexOf('\n', end + 1) + 1);
  const meta = {};
  for (const line of head.split(/\r?\n/)) {
    const m = line.match(/^(\w[\w_]*):\s*(.*)$/);
    if (!m) continue;
    let v = m[2].trim();
    if (v.startsWith('[') && v.endsWith(']')) v = v.slice(1, -1).split(',').map((s) => s.trim().replace(/^['"]|['"]$/g, ''));
    meta[m[1]] = v;
  }
  return { meta, body };
}

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function inline(s, href) {
  return esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/\*([^*]+)\*/g, '<i>$1</i>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_match, text, link) => '<a href="' + href(link) + '">' + text + '</a>');
}

function renderMd(md, href) {
  const renderInline = text => inline(text, href);
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
    if (h) { closeLists(); out.push(`<h${h[1].length}>${renderInline(h[2])}</h${h[1].length}>`); i += 1; continue; }
    if (/^(-{3,}|\*{3,})$/.test(line.trim())) { closeLists(); out.push('<hr>'); i += 1; continue; }
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ul) { if (!inUl) { closeLists(); out.push('<ul>'); inUl = true; } out.push(`<li>${renderInline(ul[1])}</li>`); i += 1; continue; }
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) { if (!inOl) { closeLists(); out.push('<ol>'); inOl = true; } out.push(`<li>${renderInline(ol[1])}</li>`); i += 1; continue; }
    if (line.startsWith('>')) { closeLists(); out.push(`<blockquote>${renderInline(line.replace(/^>\s?/, ''))}</blockquote>`); i += 1; continue; }
    if (!line.trim()) { closeLists(); i += 1; continue; }
    // 段落: 连续非空行合并
    const buf = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*[-*]\s|\s*\d+\.\s|>)/.test(lines[i])) { buf.push(lines[i]); i += 1; }
    out.push(`<p>${renderInline(buf.join(' '))}</p>`);
  }
  closeLists();
  return out.join('\n');
}

/* ---------- 布局 ---------- */
const SECTIONS = ['paths', 'guides', 'labs'];
const NAV = [
  ['home', '/'],
  ['paths', '/paths/'],
  ['guides', '/guides/'],
  ['questions', '/questions/'],
  ['labs', '/labs/'],
  ['about', '/about/'],
  ['search', '/search/'],
];

function transformAnswer(html) {
  return html.replace(/<p><b>(\d+)\. ?([^<]+)<\/b><\/p>/g, '<div class="qa-h"><span class="qa-no">$1</span><span class="qa-t">$2</span></div>');
}
const SD_MARKER_DEFS = '<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs><marker id="sd-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#5b5bd6"/></marker></defs></svg>';
function layout(locale, { page, title, desc, body, activeNav = '', extraHead = '' }) {
  const { copy, language, url } = locale;
  const other = language === 'en' ? 'zh-CN' : 'en';
  return `<!doctype html>
<html lang="${language}" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)} · agent-interview.icu</title>
<meta name="description" content="${esc(desc || copy.defaultDescription)}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="canonical" href="${SITE}${url(page)}">
${LANGUAGES.map(code => `<link rel="alternate" hreflang="${code}" href="${SITE}${localePath(code, page)}">`).join('\n')}
<link rel="alternate" hreflang="x-default" href="${SITE}${localePath('zh-CN', page)}">
${extraHead}
<style>
:root{--bg:#0f1218;--card:#161a22;--surface:#1d222c;--border:#2b323e;--text:#e7eaf0;--text2:#b9c1cd;--text3:#939dac;--primary:#8b8bf5;--primary50:#1c1e36;--green:#4ade80;--amber:#fbbf24;--radius:12px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.8 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
a{color:var(--primary);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1080px;margin:0 auto;padding:0 24px}
header.site{position:sticky;top:0;z-index:9;background:rgba(15,18,24,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--border)}
header.site .wrap{display:flex;align-items:center;gap:12px 22px;min-height:60px;padding-block:8px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;color:var(--text);font-weight:700;font-size:16px}
.brand:hover{text-decoration:none}
.brand .logo{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,#6d5df6,#8b5cf6);display:flex;align-items:center;justify-content:center;font-size:13px;color:#fff}
.brand small{display:block;font-size:9.5px;letter-spacing:.18em;color:var(--text3);font-weight:500}
nav.main{display:flex;gap:4px;flex:1;flex-wrap:wrap}
nav.main a{color:var(--text2);padding:6px 12px;border-radius:8px;font-size:14px}
nav.main a:hover{color:var(--text);background:var(--surface);text-decoration:none}
nav.main a.on{color:var(--primary);background:var(--primary50)}
.language-switch{flex:none;border:1px solid var(--border);border-radius:8px;padding:4px 9px;font-size:13px}
.search-form{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px}
.search-form input{flex:1;min-width:min(220px,100%);background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px 12px;color:var(--text);font:inherit}
.search-form button{cursor:pointer;border:0;font:inherit}
.search-results li{margin-bottom:12px}
.source-title,.source-language{color:var(--text3);font-size:13px}
.hero{padding:72px 0 56px;text-align:center;background:radial-gradient(700px 300px at 50% -40px,rgba(109,93,246,.18),transparent)}
.hero h1{font-size:34px;margin:0 0 14px;line-height:1.35}
.hero h1 .grad{background:linear-gradient(120deg,#a5a5fa,#8b5cf6);-webkit-background-clip:text;background-clip:text;color:transparent}
.hero p{color:var(--text2);font-size:16px;max-width:640px;margin:0 auto 26px}
.cta{display:inline-flex;gap:12px;justify-content:center;flex-wrap:wrap}
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
article.page{display:grid;grid-template-columns:minmax(0,1fr);gap:34px;padding:36px 0 60px;align-items:start}
article.page.with-toc{grid-template-columns:220px minmax(0,1fr)}
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
@media (max-width:900px){.grid3{grid-template-columns:1fr}.grid2{grid-template-columns:1fr}article.page.with-toc{grid-template-columns:minmax(0,1fr)}aside.toc{display:none}nav.main a{padding:6px 8px;font-size:13px}}
@media (max-width:640px){header.site .wrap{justify-content:space-between}nav.main{order:1;flex-basis:100%}.wrap{padding-inline:16px}}
</style>
</head>
<body>
<header class="site"><div class="wrap">
  <a class="brand" href="${url('/')}"><span class="logo">CP</span><span>agent-interview.icu<small>${esc(copy.brandTagline)}</small></span></a>
  <nav class="main" aria-label="${esc(copy.navLabel)}">${NAV.map(([name, href]) => `<a href="${url(href)}"${activeNav === href ? ' class="on" aria-current="' + (page === href ? 'page' : 'location') + '"' : ''}>${esc(copy.sections[name])}</a>`).join('')}</nav>
  <a class="language-switch" href="${localePath(other, page)}" lang="${other}" hreflang="${other}" aria-label="${esc(copy.languageSwitch)}">${esc(copy.otherLanguageName)}</a>
</div></header>
<main>${body}</main>
<footer class="site"><div class="wrap">
  <span>${esc(copy.footerName)}</span>
  <span>${esc(copy.footerLicense)} · <a href="${url('/about/')}">${esc(copy.footerAbout)}</a> · ${esc(copy.footerCredit)}</span>
</div></footer>
</body>
</html>`;
}


const slug = text => esc(text).toLowerCase().replace(/[^\w\u4e00-\u9fa5]+/g, '-');
const stripHtml = text => String(text).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();

function write(filename, content) {
  const path = join(DIST, filename);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, content, 'utf8');
}

function writePage(locale, page, options) {
  write(locale.url(page).slice(1) + 'index.html', layout(locale, { page, ...options }));
}

function walk(dir) {
  const files = [];
  for (const name of readdirSync(dir).sort()) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) files.push(...walk(path));
    else if (name.endsWith('.md')) files.push(path);
  }
  return files;
}

const docs = walk(CONTENT).map(path => {
  const { meta, body } = parseFrontmatter(readFileSync(path, 'utf8'));
  const rel = relative(CONTENT, path).replace(/\\/g, '/').replace(/\.md$/, '');
  const legacyUrl = '/' + rel + '/';
  const url = legacyUrl.replace(/\/index\/$/, '/');
  const title = meta.title || body.match(/^# (.+)$/m)?.[1]?.trim() || rel;
  const category = meta.category || (rel.includes('/') ? rel.split('/')[0] : 'articles');
  return { meta, body, url, legacyUrl, title, category };
});

const bank = existsSync(BANK) ? JSON.parse(readFileSync(BANK, 'utf8')).questions : [];
const questions = [];
const seen = new Set();
for (const question of bank) {
  if (questions.length >= 20) break;
  const key = question.title.slice(0, 18);
  if (seen.has(key)) continue;
  seen.add(key);
  questions.push({
    ...question,
    url: '/questions/q-' + (questions.length + 1) + '/',
    excerpt: stripHtml(question.html),
  });
}

const pages = [...NAV.map(([, url]) => url), ...docs.map(doc => doc.url), ...questions.map(question => question.url)];
const urls = [...new Set(pages)];
const aliases = new Map(docs.filter(doc => doc.url !== doc.legacyUrl).map(doc => [doc.legacyUrl, doc.url]));

function localLink(href, locale) {
  const match = href.match(/^(\/(?!\/)[^?#]*)(.*)$/);
  if (!match) return href;
  const [, path, rest] = match;
  const canonical = aliases.get(path) || path;
  return urls.includes(canonical) ? locale.url(canonical) + rest : href;
}

const latest = [...docs].sort((a, b) => (b.meta.published_at || '').localeCompare(a.meta.published_at || ''));
const featured = latest.filter(doc => doc.category === 'guides').slice(0, 6);

function documentView(doc, locale) {
  const translated = locale.copy.articles[doc.url] ?? {};
  return {
    title: translated.title || doc.title,
    description: translated.description || doc.meta.description || '',
  };
}

function articleTags(doc, locale) {
  const tags = Array.isArray(doc.meta.tags) ? doc.meta.tags : [];
  return tags.map(tag => locale.copy.articleTags[tag] || tag);
}

function documentCard(doc, locale, showTags = false) {
  const { copy, url, number } = locale;
  const view = documentView(doc, locale);
  const level = copy.levels[doc.meta.level];
  const tags = showTags ? articleTags(doc, locale) : [];
  const minutes = fill(copy.readingTime, { minutes: number(doc.meta.estimated_reading_minutes || 10) });
  return `<a class="card"${showTags ? ' style="display:block;margin-bottom:14px"' : ''} href="${url(doc.url)}">
    <h3>${esc(view.title)}</h3><p>${esc(view.description)}</p>
    <div class="meta">${level ? `<span class="tag lv">${esc(level)}</span>` : ''}<span class="tag">${esc(minutes)}</span>${tags.map(tag => `<span class="tag">#${esc(tag)}</span>`).join('')}</div>
  </a>`;
}

function timeMarkup(value, locale) {
  return `<time datetime="${esc(value)}">${esc(locale.date(value))}</time>`;
}

function sourceLanguageNotice(locale) {
  return locale.language === 'en' ? `<p class="source-language">${esc(locale.copy.sourceLanguage)}</p>` : '';
}

function docPage(doc, locale) {
  const { copy, url, number } = locale;
  const { title, description } = documentView(doc, locale);
  const category = copy.sections[doc.category] || copy.sections.articles;
  const categoryPage = SECTIONS.includes(doc.category) ? '/' + doc.category + '/' : null;
  const heading = doc.meta.title
    ? `<h1>${esc(title)}</h1>${title !== doc.title ? `<p class="source-title" lang="zh-CN">${esc(doc.title)}</p>` : ''}`
    : '';
  const html = renderMd(doc.body, href => localLink(href, locale)).replace(/<h2>(.*?)<\/h2>/g, (_match, text) =>
    `<h2 id="${slug(text.replace(/<[^>]+>/g, ''))}">${text}</h2>`);
  const toc = [...html.matchAll(/<h2 id="([^"]+)">(.*?)<\/h2>/g)]
    .map(([, id, text]) => `<a href="#${id}" lang="zh-CN">${text}</a>`).join('');
  const updated = doc.meta.updated_at || doc.meta.published_at;
  const metadata = [
    esc(category),
    copy.levels[doc.meta.level] && esc(copy.levels[doc.meta.level]),
    esc(fill(copy.estimatedReadingTime, { minutes: number(doc.meta.estimated_reading_minutes || 10) })),
    updated && fill(esc(copy.updated), { date: timeMarkup(updated, locale) }),
    articleTags(doc, locale).map(esc).join(' / '),
  ].filter(Boolean).join(' · ');
  const body = `<div class="wrap"><article class="page${toc ? ' with-toc' : ''}">
    ${toc ? `<aside class="toc" aria-label="${esc(copy.toc)}"><div class="t">${esc(copy.toc)}</div>${toc}</aside>` : ''}
    <div class="doc">
      ${heading}
      <div class="pmeta">${metadata}</div>
      ${sourceLanguageNotice(locale)}
      <div lang="zh-CN">${html}</div>
      ${categoryPage ? `<p><a href="${url(categoryPage)}">${esc(fill(copy.backToSection, { section: category }))}</a></p>` : ''}
    </div>
  </article></div>`;
  writePage(locale, doc.url, {
    title, desc: description, body, activeNav: categoryPage ?? (doc.category === 'about' ? '/about/' : ''),
  });
}

function homePage(locale) {
  const { copy, url, number } = locale;
  const body = `<div class="hero"><div class="wrap">
  <h1>${esc(copy.heroLead)}<span class="grad">${esc(copy.heroAccent)}</span>${esc(copy.heroTail)}</h1>
  <p>${esc(copy.heroDescription)}</p>
  <div class="cta">
    <a class="btn primary" href="${url('/paths/ai-agent-engineer-0-1/')}">${esc(copy.startPath)}</a>
    <a class="btn ghost" href="${url('/guides/')}">${esc(copy.browseGuides)}</a>
  </div>
</div></div>
<div class="wrap">
<section class="block">
  <div class="grid3">
    <a class="card" href="${url('/paths/')}"><h3>🗺 ${esc(copy.sections.paths)}</h3><p>${esc(copy.pathCard)}</p></a>
    <a class="card" href="${url('/guides/')}"><h3>📚 ${esc(copy.sections.guides)}</h3><p>${esc(copy.guideCard)}</p></a>
    <a class="card" href="${url('/questions/')}"><h3>📝 ${esc(copy.sections.questions)}</h3><p>${esc(fill(copy.questionCard, { count: number(questions.length) }))}</p></a>
  </div>
</section>
<section class="block">
  <h2>${esc(copy.featured)}</h2>
  <div class="grid3">${featured.map(doc => documentCard(doc, locale)).join('')}</div>
</section>
<section class="block">
  <h2>${esc(copy.popular)}</h2>
  <div class="topic-cloud">${Object.values(copy.topics).map(topic => `<a href="${url('/guides/')}">${esc(topic)}</a>`).join('')}</div>
</section>
<section class="block">
  <h2>${esc(copy.latest)}</h2>
  <ul class="updates">
    ${latest.slice(0, 8).map(doc => `<li>${doc.meta.published_at ? timeMarkup(doc.meta.published_at, locale) : ''}
      <a href="${url(doc.url)}">${esc(documentView(doc, locale).title)}</a><span style="color:var(--text3)">· ${esc(copy.sections[doc.category] || copy.sections.articles)}</span></li>`).join('')}
  </ul>
</section>
<section class="block">
  <div class="cp-cta">
    <h2 style="margin:0 0 10px">${esc(copy.careerTitle)}</h2>
    <p style="color:var(--text2);max-width:560px;margin:0 auto 20px">${esc(copy.careerDescription)}</p>
    <a class="btn primary" href="${url('/about/')}">${esc(copy.aboutCareerPilot)}</a>
  </div>
</section>
</div>`;
  writePage(locale, '/', { title: copy.sections.home, desc: copy.homeDescription, body, activeNav: '/' });
}

function sectionPage(category, locale) {
  const { copy } = locale;
  const page = '/' + category + '/';
  const title = copy.sections[category];
  const desc = copy.sectionDescriptions[category];
  const cards = docs.filter(doc => doc.category === category).map(doc => documentCard(doc, locale, true));
  const body = `<div class="wrap"><div class="list-head"><h1>${esc(title)}</h1><p>${esc(desc)}</p></div>
  <section class="block">${cards.join('')}</section></div>`;
  writePage(locale, page, { title, desc, body, activeNav: page });
}

function questionDifficulty(question, locale) {
  const count = Math.min(5, question.difficulty || 3);
  return `<span aria-label="${esc(fill(locale.copy.difficulty, { count: locale.number(count) }))}">${'★'.repeat(count)}</span>`;
}

function questionPages(locale) {
  const { copy, url, number } = locale;
  const topicName = question => copy.questionTopics[question.topic] || question.topic;
  const list = questions.map((question, i) => `<div class="q-item">
    <h3><a href="${url(question.url)}">${number(i + 1)}. <span lang="zh-CN">${esc(question.title)}</span></a></h3>
    <p lang="zh-CN">${esc(question.excerpt.slice(0, 110))}…</p>
    <div class="meta"><span class="tag">${esc(topicName(question))}</span><span class="tag">${questionDifficulty(question, locale)}</span>${question.company ? `<span class="tag">${esc(question.company)}</span>` : ''}</div>
  </div>`).join('');
  const title = copy.sections.questions;
  const count = fill(copy.publicQuestionCount, { count: number(questions.length), total: number(bank.length) });
  const body = `<div class="wrap"><div class="list-head"><h1>${esc(title)}</h1><p>${esc(count)}</p></div>
  <section class="block">${list}</section></div>`;
  writePage(locale, '/questions/', { title, desc: copy.questionListDescription, body, activeNav: '/questions/' });

  questions.forEach((question, i) => {
    const topic = topicName(question);
    const body = `<div class="wrap"><article class="page"><div class="doc">
      <h1 lang="zh-CN">${esc(question.title)}</h1>
      <div class="pmeta">${esc(copy.sections.questions)} · ${esc(topic)} · ${questionDifficulty(question, locale)}${question.company ? ' · ' + esc(question.company) : ''}</div>
      ${sourceLanguageNotice(locale)}
      <div class="bank-answer" lang="zh-CN">${SD_MARKER_DEFS}${transformAnswer(question.html)}</div>
      <p style="margin-top:26px"><a href="${url('/questions/')}">${esc(copy.backToQuestions)}</a></p>
    </div></article></div>`;
    writePage(locale, question.url, {
      title: fill(copy.questionTitle, { number: number(i + 1), title: question.title }),
      desc: fill(copy.questionDescription, { number: number(i + 1), topic }), body, activeNav: '/questions/',
    });
  });
}

function searchIndex(locale) {
  const { copy, url, language } = locale;
  return [
    ...docs.map(doc => {
      const view = documentView(doc, locale);
      const tags = Array.isArray(doc.meta.tags) ? doc.meta.tags : [];
      return {
        t: view.title, d: view.description, u: url(doc.url), c: doc.category,
        k: [doc.title, doc.meta.description, ...tags, ...articleTags(doc, locale), stripHtml(renderMd(doc.body, href => href))].filter(Boolean).join(' '),
        l: view.title === doc.title ? 'zh-CN' : language,
      };
    }),
    ...questions.map(question => ({
      t: question.title, d: question.excerpt.slice(0, 80), u: url(question.url), c: 'questions',
      k: [question.excerpt, question.company, copy.questionTopics[question.topic], question.topic].filter(Boolean).join(' '),
      l: 'zh-CN',
    })),
  ];
}

function searchPage(locale) {
  const { copy, url, language } = locale;
  const config = JSON.stringify({
    language, index: url('/search.json'), categories: copy.sections, loading: copy.searchLoading,
    empty: copy.searchNoResults, one: copy.searchOneResult, many: copy.searchManyResults, unavailable: copy.searchUnavailable,
  }).replace(/</g, '\\u003c');
  const body = `<div class="wrap"><div class="list-head"><h1>${esc(copy.sections.search)}</h1>
    <p>${esc(copy.searchDescription)}</p>
    <form class="search-form" action="${url('/search/')}" role="search">
      <input name="q" type="search" required aria-label="${esc(copy.searchLabel)}" placeholder="${esc(copy.searchPlaceholder)}">
      <button class="btn primary">${esc(copy.searchAction)}</button>
    </form>
  </div>
  <section class="block">
    <p id="search-status" role="status">${esc(copy.searchPrompt)}</p>
    <ul id="search-results" class="search-results"></ul>
  </section>
</div>
<script type="module">
const settings = ${config};
const search = new URLSearchParams(location.search);
const query = search.get('q')?.trim() || '';
const status = document.querySelector('#search-status');
if (query) {
  document.querySelector('input[name="q"]').value = query;
  document.querySelector('.language-switch').search = '?' + new URLSearchParams({ q: query });
  status.textContent = settings.loading;
  try {
    const response = await fetch(settings.index);
    if (!response.ok) throw new Error('Search index unavailable');
    const terms = query.toLocaleLowerCase(settings.language).split(/\\s+/);
    const rows = (await response.json()).filter(row => {
      const text = [row.t, row.d, settings.categories[row.c], row.k].join(' ').toLocaleLowerCase(settings.language);
      return terms.every(term => text.includes(term));
    });
    if (rows.length) {
      const label = rows.length === 1 ? settings.one : settings.many;
      status.textContent = label.replace('{count}', new Intl.NumberFormat(settings.language).format(rows.length));
    } else {
      status.textContent = settings.empty.replace('{query}', () => query);
    }
    const fragment = document.createDocumentFragment();
    for (const row of rows) {
      const item = document.createElement('li');
      const link = document.createElement('a');
      link.href = row.u;
      link.lang = row.l;
      link.textContent = row.t;
      const category = document.createElement('small');
      category.textContent = ' · ' + (settings.categories[row.c] || settings.categories.articles);
      const description = document.createElement('div');
      if (row.c === 'questions') description.lang = 'zh-CN';
      description.textContent = row.d;
      item.append(link, category, description);
      fragment.append(item);
    }
    document.querySelector('#search-results').replaceChildren(fragment);
  } catch {
    status.textContent = settings.unavailable;
  }
}
</script>`;
  writePage(locale, '/search/', { title: copy.sections.search, desc: copy.searchDescription, body, activeNav: '/search/' });
  write(url('/search.json').slice(1), JSON.stringify(searchIndex(locale)));
}

for (const language of LANGUAGES) {
  const locale = loadLocale(language);
  homePage(locale);
  for (const category of SECTIONS) sectionPage(category, locale);
  for (const doc of docs) docPage(doc, locale);
  questionPages(locale);
  searchPage(locale);
  for (const [legacy, canonical] of aliases) {
    const title = documentView(docs.find(doc => doc.url === canonical), locale).title;
    const target = locale.url(canonical);
    write(locale.url(legacy).slice(1) + 'index.html', layout(locale, {
      page: canonical, title, extraHead: `<meta http-equiv="refresh" content="0; url=${target}">`,
      body: `<div class="wrap"><a href="${target}">${esc(title)}</a></div>`,
    }));
  }
}

write('sitemap.xml', `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">
${urls.flatMap(page => LANGUAGES.map(language => `  <url>
    <loc>${esc(SITE + localePath(language, page))}</loc>
    ${LANGUAGES.map(code => `<xhtml:link rel="alternate" hreflang="${code}" href="${esc(SITE + localePath(code, page))}"/>`).join('\n    ')}
  </url>`)).join('\n')}
</urlset>
`);
write('robots.txt', `User-agent: *\nAllow: /\nSitemap: ${SITE}/sitemap.xml\n`);
write('favicon.svg', '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#6d5df6"/><text x="16" y="21" text-anchor="middle" font-family="sans-serif" font-size="14" font-weight="700" fill="white">CP</text></svg>');

console.log(`[portal] ${urls.length} pages per language, ${docs.length} articles and ${questions.length} public questions → ${DIST}`);
