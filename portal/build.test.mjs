import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, test } from 'node:test';
import { fileURLToPath } from 'node:url';

const site = 'https://agent-interview.icu';
const scratch = join(tmpdir(), 'careerpilot-portal-');
let dist;
before(() => {
  dist = mkdtempSync(scratch);
  execFileSync(process.execPath, [fileURLToPath(new URL('./build.mjs', import.meta.url))], {
    env: { ...process.env, PORTAL_DIST: dist },
    encoding: 'utf8',
  });
});
after(() => {
  assert.ok(dist.startsWith(scratch));
  rmSync(dist, { recursive: true, force: true });
});

const read = path => readFileSync(join(dist, path), 'utf8');
const fileForUrl = url => url.endsWith('/') ? url + 'index.html' : url;
const guide = '/guides/tool-retry-idempotency/';
const question = '/questions/q-1/';
const internalLinks = html => [...html.matchAll(/\bhref="(\/(?!\/)[^"#?]*)[^"]*"/g)].map(match => match[1]);

test('every canonical page has a static counterpart and resolvable internal links', () => {
  const sitemap = read('sitemap.xml');
  const paths = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map(match => new URL(match[1]).pathname);
  assert.equal(new Set(paths).size, paths.length);
  assert.match(read('robots.txt'), /Sitemap: https:\/\/agent-interview\.icu\/sitemap\.xml/);
  for (const page of ['/', '/paths/', '/guides/', '/questions/', '/labs/', '/about/', '/search/', guide, question]) {
    assert.ok(paths.includes(page), page);
    assert.ok(paths.includes('/en' + page), '/en' + page);
  }

  for (const path of paths) {
    const english = path.startsWith('/en/');
    const other = english ? path.slice(3) : '/en' + path;
    const html = read(fileForUrl(path));
    assert.ok(html.includes('lang="' + (english ? 'en' : 'zh-CN') + '" data-theme'), path);
    assert.ok(html.includes('class="language-switch" href="' + other + '"'), path);
    assert.ok(html.includes('rel="canonical" href="' + site + path + '"'), path);
    assert.ok(html.includes('rel="alternate" hreflang="' + (english ? 'zh-CN' : 'en') + '" href="' + site + other + '"'), path);
    for (const href of internalLinks(html)) {
      assert.ok(existsSync(join(dist, fileForUrl(href))), path + ' → ' + href);
      if (english) assert.ok(href === '/favicon.svg' || href === other || href.startsWith('/en/'), path + ' → ' + href);
    }
  }
  assert.match(read('/about/index/index.html'), /http-equiv="refresh" content="0; url=\/about\/"/);
  assert.match(read('/en/about/index/index.html'), /http-equiv="refresh" content="0; url=\/en\/about\/"/);
});

test('English metadata and navigation keep the original article and question text', () => {
  const chineseGuide = read(fileForUrl(guide));
  const englishGuide = read(fileForUrl('/en' + guide));
  assert.match(chineseGuide, /<title>工具调用失败重试的工程实践/);
  assert.match(englishGuide, /<p class="source-title" lang="zh-CN">工具调用失败重试的工程实践/);
  assert.match(englishGuide, /<title>Retrying failed tool calls in AI agents/);
  assert.match(englishGuide, /name="description" content="Make tool-call failures predictable/);
  assert.match(englishGuide, /On this page/);
  assert.match(englishGuide, /href="#一句话结论"/);
  assert.match(englishGuide, /id="一句话结论"/);
  assert.match(englishGuide, /href="\/en\/guides\/observability-for-agents\/"/);
  assert.match(englishGuide, /href="\/en\/guides\/">← Back to Technical guides/);

  const originalBody = html => html.split('<div lang="zh-CN">')[1].split('</div>')[0].replaceAll('href="/en/', 'href="/');
  assert.equal(originalBody(englishGuide), originalBody(chineseGuide));
  assert.match(read('/tool-call-safe-retry/index.html'), /<title>AI Agent 工具调用安全重试指南/);

  const chineseQuestion = read(fileForUrl(question));
  const englishQuestion = read(fileForUrl('/en' + question));
  assert.match(englishQuestion, /Interview question 1 · 设计日均100万查询的企业级RAG系统/);
  assert.match(englishQuestion, /RAG and context engineering/);
  assert.match(englishQuestion, /href="\/en\/questions\/">← Back to interview questions/);
  const answer = html => html.split('<div class="bank-answer" lang="zh-CN">')[1].split('\n      <p style="margin-top:26px">')[0];
  assert.equal(answer(englishQuestion), answer(chineseQuestion));
});

test('both search indexes keep original terms and point to the matching locale', () => {
  const chinese = JSON.parse(read('/search.json'));
  const english = JSON.parse(read('/en/search.json'));
  assert.equal(english.length, chinese.length);
  const recall = english.find(row => row.u === '/en/guides/rag-recall-debug/');
  assert.equal(recall.t, 'Troubleshooting low recall in RAG');
  assert.match(recall.k, /RAG 召回率差怎么排查/);
  for (const [i, row] of english.entries()) {
    assert.equal(row.u, '/en' + chinese[i].u);
    assert.ok(existsSync(join(dist, fileForUrl(row.u))), row.u);
    assert.ok([row.t, row.k].join(' ').includes(chinese[i].t), chinese[i].t);
    if (row.c === 'questions') assert.equal(row.t, chinese[i].t);
  }
  assert.match(read('/search/index.html'), /action="\/search\/"/);
  assert.match(read('/en/search/index.html'), /action="\/en\/search\/"/);
  assert.match(read('/en/search/index.html'), /"index":"\/en\/search\.json"/);
});
