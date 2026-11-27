const assert = require('node:assert/strict');
const { writeFile } = require('node:fs/promises');
const { join } = require('node:path');

const playwright = process.env.STUDY_PLAYWRIGHT ||
  join(process.env.LOCALAPPDATA, 'npm-cache/_npx/31e32ef8478fbf80/node_modules/playwright');
const { chromium } = require(playwright);
const output = process.env.STUDY_EVIDENCE;
const secrets = JSON.parse(process.env.STUDY_REDACT || '[]');
const web = 'http://127.0.0.1:18860';
const api = 'http://127.0.0.1:18861/api/v1';
const evidence = { stages: [], writes: {}, replays: [] };
const timeout = 240_000;
const initial = 'RAG 检索中的召回评估，precision@5 和 recall@5 的分母分别是什么？';
const revised = 'Precision@5 是前 5 条返回中相关结果的占比，分母是 5。Recall@5 是前 5 条命中的相关结果数占标注集中全部相关结果的比例，分母是全部相关结果数。';

function redact(value) {
  let raw = JSON.stringify(value, null, 2);
  for (const secret of secrets.filter(Boolean)) raw = raw.split(secret).join('[redacted]');
  return raw;
}

async function save() {
  await writeFile(join(output, 'browser.json'), redact(evidence));
}

async function run() {
  const browser = await chromium.launch({ channel: 'msedge' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.addInitScript(({ token, workspace }) => {
      sessionStorage.setItem('cp-login-workspace', workspace);
      sessionStorage.setItem('cp-login-token', token);
    }, { token: process.env.API_TOKEN, workspace: process.env.DEFAULT_WORKSPACE_SLUG });
    page.setDefaultTimeout(timeout);
    await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ?
      route.continue() : route.abort());
    const button = name => page.getByRole('button', { name, exact: typeof name === 'string' });
    const visible = locator => locator.waitFor({ state: 'visible' });
    let headers;
    async function stage(name) {
      evidence.stages.push(name);
      console.log(name);
      await save();
    }
    async function write(label, method, pattern, action) {
      const [res] = await Promise.all([
        page.waitForResponse(response => {
          const path = new URL(response.url()).pathname.replace('/api/v1', '');
          return response.request().method() === method && pattern.test(path);
        }),
        action(),
      ]);
      const request = res.request();
      const sent = request.postDataJSON();
      const path = new URL(res.url()).pathname.replace('/api/v1', '');
      const body = await res.json();
      const h = await request.allHeaders();
      headers = Object.fromEntries(Object.entries(h).filter(([key]) => /^x-|^authorization$/.test(key)));
      evidence.writes[label] = { method, path, sent, status: res.status(), body };
      await save();
      assert.ok(res.ok(), label + ' returned HTTP ' + res.status());
      return body;
    }
    async function local(method, path, data) {
      const res = await page.request.fetch(api + path, { method, headers, data });
      assert.ok(res.ok(), method + ' ' + path + ' returned HTTP ' + res.status());
      return res.json();
    }
    async function replay(label, select) {
      const { method, path, sent, body } = evidence.writes[label];
      const again = await local(method, path, sent ?? undefined);
      const identity = select(body);
      assert.ok(identity !== undefined && identity !== null, label + ' has no replay identity');
      assert.deepEqual(select(again), identity, label + ' replay changed identity');
      evidence.replays.push(label);
      await save();
    }
    const id = value => value.id;
    const lastSubmission = value => value.submissions.at(-1).submission_id;

    await page.goto(web + '/study/chat');
    await page.getByLabel('学习问题').fill(initial);
    await write('message', 'POST', /^\/study\/sessions\/\d+\/messages$/, () => button('开始学习').click());
    await page.waitForURL(/\/study\/chat\/\d+$/);
    const chat = page.url();
    const session = new URL(chat).pathname.split('/').at(-1);
    const assistant = page.locator('article.study-turn.assistant');
    await visible(assistant.nth(0).getByText(/已保存/));
    assert.match(await assistant.nth(0).innerText(), /precision|准确率|精确率/i);
    await replay('message', value => value.assistant?.id ?? value.assistant_message_id ?? value.message_id ?? value.id);
    await page.getByLabel('继续问').fill('追问：如果返回 5 条，其中 3 条相关，但标注共有 6 条相关，请算出两个数值并解释差别。');
    await button('发送').click();
    await visible(assistant.nth(1).getByText(/已保存/));
    await stage('two real chat replies');
    await page.screenshot({ path: join(output, 'chat.png') });

    await write('note-job', 'POST', /\/note-jobs$/, () => button('整理成笔记').click());
    await replay('note-job', id);
    await page.getByRole('link', { name: '打开笔记' }).click();
    const title = page.getByLabel('标题', { exact: true });
    const edited = 'STUDY-ACCEPTANCE · RAG 召回评估';
    await title.fill(edited);
    await write('note-edit', 'PATCH', /\/notes\/\d+$/, () => button('保存笔记').click());
    await visible(page.getByText('笔记已保存。'));
    await page.reload();
    await visible(title);
    assert.equal(await title.inputValue(), edited);
    const cards = await write('cards', 'POST', /\/notes\/\d+\/cards$/, () => button(/从已保存版本 .* 生成卡片/).click());
    await replay('cards', value => value.items.map(id));
    assert.ok(cards.items.length > 0);
    const card = page.getByRole('listitem').filter({ has: button('启用卡片') }).first();
    await visible(card);
    const revealed = await write('reveal', 'POST', /\/cards\/\d+\/reveal$/, () => card.getByRole('button', { name: '揭晓答案', exact: true }).first().click());
    assert.ok(revealed.answer);
    const activated = await write('activate', 'PATCH', /\/cards\/\d+$/, () => card.getByRole('button', { name: '启用卡片', exact: true }).first().click());
    assert.equal(activated.id, revealed.id);
    await stage('edited note persisted and generated card activated');
    await page.screenshot({ path: join(output, 'notes.png') });

    const practice = await write('practice', 'POST', /\/sessions\/\d+\/practices$/, () => button('主动练习').first().click());
    await page.waitForURL(/\?card=\d+&practice=quiz/);
    const panel = page.getByRole('region', { name: '主动练习' });
    assert.equal(practice.skill_key, 'rag');
    assert.equal(practice.card_id, revealed.id);
    await replay('practice', id);
    await panel.getByLabel('我的原答').fill('我认为两个指标都用返回条数作为分母。');
    await write('original', 'POST', /\/practices\/\d+\/answers$/, () => button('提交原答').click());
    await replay('original', lastSubmission);
    await visible(panel.getByText('首次原答 · 已评价', { exact: true }));
    const hint = await write('hint', 'POST', /\/practices\/\d+\/assistance$/, () => button('请求提示').click());
    assert.ok(hint.content);
    await replay('hint', value => value.id ?? value.event_id ?? value.content);
    await panel.getByLabel('修正回答').fill(revised);
    await write('revision', 'POST', /\/practices\/\d+\/answers$/, () => button('提交修正').click());
    await replay('revision', lastSubmission);
    await visible(panel.getByText('修正 #2 · 已评价', { exact: true }));
    await stage('original and assisted revision scored');
    await page.screenshot({ path: join(output, 'practice.png') });

    const exam = await write('retest', 'POST', /\/practices\/\d+\/retest$/, () => button('生成新情境复测').click());
    await replay('retest', id);
    const retestPath = '/study/practices/' + practice.id + '/retest';
    evidence.before = await local('GET', retestPath);
    assert.equal(evidence.before.evidence_ids.length, 0);
    await page.getByRole('link', { name: '打开新情境复测' }).click();
    assert.equal(exam.questions.length, 1);
    const question = exam.questions[0].title;
    await visible(page.getByText(question, { exact: true }));
    const numbers = question.match(/返回 (\d+) 条，其中 (\d+) 条相关，标注共有 (\d+) 条相关/);
    assert.ok(numbers, 'Expected one of the new synthetic bank scenarios');
    const [, returned, hits, total] = numbers;
    const answer = `precision@${returned} = ${hits}/${returned} = ${hits / returned}，分母是返回条数。` +
      `recall@${returned} = ${hits}/${total} = ${hits / total}，分母是标注的全部相关结果数。`;
    await page.getByPlaceholder(/输入你的回答/).fill(answer);
    await button('提交答卷').click();
    await write('exam-submit', 'POST', /\/exams\/\d+\/submit$/, () => button('确认提交').click());
    await visible(page.getByText(/考试 #\d+ · 本次评分已完成/).first());
    evidence.retest = await local('GET', retestPath);
    assert.equal(evidence.retest.status, 'COMPLETED');
    await stage('formal new-situation exam scored');
    await page.screenshot({ path: join(output, 'exam.png') });

    await page.goto(chat);
    await visible(panel.getByRole('link', { name: '打开新情境复测' }));
    await write('finish-note-job', 'POST', /\/note-jobs$/, () => button('结束并整理').click());
    await visible(page.getByText('会话已结束，可随时继续'));
    const completed = await local('GET', '/study/sessions/' + session);
    assert.equal(completed.status, 'ended');
    await visible(page.getByRole('link', { name: '打开笔记' }));
    await stage('study session ended');
    await page.screenshot({ path: join(output, 'finished.png') });
  } catch (error) {
    evidence.failure = String(error.message).slice(0, 2000);
    await save();
    const page = browser.contexts()[0]?.pages()[0];
    if (page) await page.screenshot({ path: join(output, 'failure.png'), timeout: 5000 }).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
}

run().catch(async error => { evidence.failure ||= String(error.message).slice(0,1600); await save(); console.error(redact(evidence.failure)); process.exitCode = 1; });
