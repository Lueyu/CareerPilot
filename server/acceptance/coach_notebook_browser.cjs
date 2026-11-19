const { chromium } = require(process.env.STUDY_PLAYWRIGHT || 'playwright');
const fs = require('node:fs');
const { join } = require('node:path');
const assert = require('node:assert/strict');

const workspace = process.env.STUDY_WORKSPACE;
const directory = process.env.STUDY_EVIDENCE;
const base = process.env.STUDY_WEB || 'http://127.0.0.1:5173';
const phase = process.argv[2];
assert.match(workspace ?? '', /^accept-coach-/);
assert.ok(directory);
assert.ok(['prepare', 'finish'].includes(phase), 'Pass prepare or finish explicitly.');
fs.mkdirSync(directory, { recursive: true });
const evidencePath = join(directory, 'evidence.json');
const journal = fs.existsSync(evidencePath) ? JSON.parse(fs.readFileSync(evidencePath)) : { workspace };
assert.equal(journal.workspace, workspace);
const save = () => fs.writeFileSync(evidencePath, JSON.stringify(journal, null, 2));

async function run(page) {
  page.setDefaultTimeout(130000);
  await page.addInitScript(slug => localStorage.setItem('cp-workspace', slug), workspace);
  const go = path => page.goto(base + path);
  const button = name => page.getByRole('button', { name, exact: true });
  const link = name => page.getByRole('link', { name, exact: true });
  const screenshot = (name, fullPage = false) => page.screenshot({ path: join(directory, name + '.png'), fullPage });
  const replies = page.locator('article.study-turn.assistant');
  const capture = async (key, pattern, method, action) => {
    if (journal[key]) return journal[key];
    const [response] = await Promise.all([page.waitForResponse(response =>
      response.request().method() === method && pattern.test(new URL(response.url()).pathname)), action()]);
    assert.ok(response.ok(), `${key} ${response.status()} ${response.ok() ? '' : (await response.text()).slice(0, 500)}`);
    journal[key] = await response.json();
    save();
    console.log(key, journal[key].id ?? journal[key].task_id ?? 'ok');
    return journal[key];
  };

  if (phase === 'prepare') {
    if (!journal.session) {
      await go('/study/chat');
      await page.getByLabel('学习问题').fill('RAG 检索计量尺：precision 和 recall 分别以什么作分母？请举一个例子。');
      await capture('session', /\/study\/sessions$/, 'POST', () => button('开始学习').click());
      await page.waitForURL(/\/study\/chat\/\d+$/);
    } else {
      await go('/study/chat/' + journal.session.id);
    }
    await replies.first().getByText(/已保存/).waitFor();
    if (!journal.follow) {
      await page.getByLabel('继续问').fill('如果返回5条，其中3条相关，标注共有6条相关，两者分别是多少？');
      await capture('follow', /\/messages$/, 'POST', () => button('发送').click());
    }
    const reply = replies.nth(1);
    await reply.getByText(/已保存/).waitFor();
    if (!journal.noteJob) {
      await capture('noteJob', /\/note-jobs$/, 'POST', () => reply.getByRole('button', { name: '整理这条回复', exact: true }).click());
    }
    const noteLink = reply.getByRole('link', { name: '打开笔记', exact: true });
    if (!await noteLink.isVisible()) {
      await button('笔记与练习').click();
      const tools = page.locator('.coach-tools');
      const retryNote = tools.getByRole('button', { name: '重试整理', exact: true });
      const finishedNote = tools.getByRole('link', { name: '打开笔记', exact: true });
      await retryNote.or(finishedNote).first().waitFor();
      if (await retryNote.isVisible() && !journal.noteRetried) {
        await capture('noteRetried', /\/note-jobs\/\d+\/retry$/, 'POST', () => retryNote.click());
        await retryNote.waitFor({ state: 'hidden' });
      }
      await finishedNote.waitFor();
      await tools.getByRole('button', { name: '关闭', exact: true }).first().click();
    }
    await noteLink.waitFor();
    const compareLink = await reply.getByRole('link', { name: '对照原聊', exact: true }).getAttribute('href');
    const replyId = Number((await reply.getAttribute('id')).split('-').at(-1));
    assert.equal(Number(new URL(compareLink, base).searchParams.get('message')), replyId);
    await screenshot('chat');
    await noteLink.click();
    await page.waitForURL(/\/study\/notes\/\d+$/);
    journal.note = Number(new URL(page.url()).pathname.split('/').at(-1));
    save();
    const focus = link('专注笔记');
    const transcript = page.getByRole('region', { name: '会话消息', exact: true });
    const columns = () => page.locator('.study-notebook-layout').evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ').length);
    const title = page.getByLabel('标题', { exact: true });
    await title.waitFor();
    assert.equal(await focus.getAttribute('aria-current'), 'page');
    assert.equal(await transcript.count(), 0);
    assert.equal(await columns(), 1);
    if (!journal.edited) {
      await title.fill('验收 · 检索计量尺');
      await capture('edited', /\/study\/notes\/\d+$/, 'PATCH', () => button('保存笔记').click());
      await page.reload();
    }
    assert.equal(await title.inputValue(), '验收 · 检索计量尺');
    await screenshot('a-focus-desktop');
    await go(compareLink);
    await transcript.waitFor();
    assert.equal(await columns(), 2);
    const target = transcript.locator('article.is-focused').getByRole('link', { name: '原消息 ↗', exact: true });
    assert.match(await target.getAttribute('href'), new RegExp('message=' + replyId + '$'));
    await screenshot('b-compare-desktop');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await columns(), 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), 0);
    await screenshot('b-compare-mobile', true);
    await focus.click();
    assert.equal(await columns(), 1);
    assert.equal(await transcript.count(), 0);
    await screenshot('a-focus-mobile');
    journal.layout = { replyId, compareLink, desktopFocus: 1, desktopCompare: 2, mobileFocus: 1, mobileCompare: 1 };
    save();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await capture('cards', /\/cards$/, 'POST', () => page.getByRole('button', { name: /从已保存版本 .* 生成卡片/ }).click());
    const cards = journal.cards.items;
    const chosen = journal.activated ? cards.find(card => card.id === journal.activated.id) :
      cards.find(card => /分母/.test(card.question) && /precision/i.test(card.question) && /recall/i.test(card.question));
    assert.ok(chosen, 'Generated note must have a precision/recall denominators card.');
    await link('查看复习卡片 →').click();
    await page.waitForURL(/\/study\/notes\?tab=review$/);
    if (!journal.activated) {
      const row = page.getByRole('listitem').filter({ hasText: chosen.question }).filter({ has: button('启用卡片') });
      await row.getByRole('button', { name: '揭晓答案', exact: true }).waitFor();
      await capture('activated', /\/cards\/\d+$/, 'PATCH', () => row.getByRole('button', { name: '启用卡片', exact: true }).click());
    }
    await screenshot('review');
    console.log('Prepared without starting practice.');
    return;
  }

  assert.ok(journal.session && journal.note && journal.activated, 'Run prepare before finish.');
  const chat = '/study/chat/' + journal.session.id;
  const cardId = journal.activated.id;
  if (!journal.practice) {
    await go('/study/notes?tab=review');
    const question = journal.cards.items.find(card => card.id === cardId).question;
    const row = page.getByRole('listitem').filter({ hasText: question }).filter({ has: button('主动练习') }).last();
    await capture('practice', /\/sessions\/\d+\/practices$/, 'POST', () => row.getByRole('button', { name: '主动练习', exact: true }).click());
  } else {
    await go(chat + '?card=' + cardId + '&practice=quiz');
  }
  const practiceUrl = new URL(page.url());
  assert.equal(practiceUrl.pathname, chat);
  assert.equal(practiceUrl.searchParams.get('card'), String(cardId));
  assert.equal(journal.practice.skill_key, 'rag');
  assert.match(journal.practice.source.retest_topic, /Precision.*Recall/i);
  const panel = page.getByRole('region', { name: '主动练习', exact: true });
  const panelButton = name => panel.getByRole('button', { name, exact: true });
  if (!journal.original) {
    await panel.getByLabel('我的原答').fill('precision、recall、向量、embedding。两个指标都以返回条数为分母。');
    await capture('original', /\/answers$/, 'POST', () => panelButton('提交原答').click());
  }
  await panel.getByText('首次原答 · 已评价', { exact: true }).waitFor();
  if (!journal.hint) await capture('hint', /\/assistance$/, 'POST', () => panelButton('请求提示').click());
  if (!journal.revision) {
    await panel.getByLabel('修正回答').fill('precision 分母是返回条数，recall 分母是标注集合的全部相关条数；例如前5条命中3条，标注共有6条，分别为3/5和3/6。');
    await capture('revision', /\/answers$/, 'POST', () => panelButton('提交修正').click());
  }
  await panel.getByText('修正 #2 · 已评价', { exact: true }).waitFor();
  await screenshot('practice');
  const exam = journal.retest || await capture('retest', /\/practices\/\d+\/retest$/, 'POST', () => panelButton('生成新情境复测').click());
  const examPath = '/study/retest?id=' + exam.id;
  const reportPath = '/study/retest/report?id=' + exam.id;
  if (!journal.submitted) {
    await panel.getByRole('link', { name: '打开新情境复测', exact: true }).click();
    await page.waitForURL(base + examPath);
    const question = exam.questions[0];
    assert.match(question.short_code, new RegExp('^QA-' + workspace.split('-').at(-1) + '-'));
    const values = question.title.match(/返回 (\d+) 条，其中 (\d+) 条相关，标注共有 (\d+) 条相关/);
    assert.ok(values, 'Expected an independent precision/recall question, got: ' + question.title);
    const [, returned, hits, relevant] = values;
    await page.getByText(question.title, { exact: true }).first().waitFor();
    await screenshot('retest-question');
    await page.getByPlaceholder(/输入你的回答/).fill(`precision = ${hits}/${returned}，以返回条数 ${returned} 为分母。recall = ${hits}/${relevant}，分母是标注集合的全部相关数 ${relevant}。两者的分子都是返回结果中实际相关的 ${hits} 条；前者衡量返回结果的纯度，后者衡量标注相关结果找回了多少。`);
    await button('提交答卷').click();
    await capture('submitted', /\/exams\/\d+\/submit$/, 'POST', () => button('确认提交').click());
  } else {
    await go(reportPath);
  }
  await page.waitForURL(base + reportPath);
  const completed = page.getByText(/本次评分已完成/).first();
  const retry = button('重新评分失败题目');
  await completed.or(retry).first().waitFor({ timeout: 330000 });
  if (await retry.isVisible() && !journal.retried) {
    await screenshot('retest-before-retry');
    await capture('retried', /\/exams\/\d+\/rescore$/, 'POST', () => retry.click());
    await retry.waitFor({ state: 'hidden' });
    await completed.or(retry).first().waitFor({ timeout: 330000 });
  }
  journal.retestReportUrl = page.url();
  journal.completed = await completed.isVisible();
  await screenshot(journal.completed ? 'exam' : 'retest-incomplete');
  save();
  assert.ok(journal.completed, 'Formal scoring did not complete after the bounded UI retry.');
}

(async () => {
  const browser = await chromium.launch({ channel: process.env.STUDY_BROWSER || 'msedge' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  try {
    await run(page);
    delete journal.error;
  } catch (error) {
    journal.error = String(error.message).slice(0, 1500);
    await page.screenshot({ path: join(directory, 'failure.png'), timeout: 5000 }).catch(() => {});
    throw error;
  } finally {
    save();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
