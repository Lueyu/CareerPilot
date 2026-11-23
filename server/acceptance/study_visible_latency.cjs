// Measure the real React chat. Both origins must serve the same synthetic workspace.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');

function loopback(value) {
  const url = new URL(value);
  if (!['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) throw Error('Expected a loopback origin');
  return url.origin;
}

async function run() {
  const ui = loopback(process.env.STUDY_WEB_ORIGIN);
  const api = loopback(process.env.STUDY_API_ORIGIN);
  const workspace = process.env.STUDY_EVAL_WORKSPACE;
  const token = process.env.STUDY_EVAL_TOKEN;
  const out = process.argv[2];
  if (!workspace || !token || !out) throw Error('Provide a synthetic workspace, token and output JSON path');
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const headers = { 'X-Workspace': workspace, 'X-Api-Token': token };
  const route = api + '/api/v1/study/sessions';
  const prompt = '用一句话说明面试通过率的分母。';
  const record = { ui, api, mode: 'auto', metric: 'send_click_to_visible_answer_ms' };
  try {
    const created = await page.request.post(route, { headers, data: { title: '首字延迟验收', mode: 'auto', source_type: 'free' } });
    if (!created.ok()) throw Error('Create session returned ' + created.status());
    const id = (await created.json()).id;
    await page.goto(ui + '/study/chat/' + id);
    await page.locator('footer textarea').fill(prompt);
    const send = page.getByRole('button', { name: '发送', exact: true });
    await send.evaluate(button => {
      button.addEventListener('click', () => {
        const start = performance.now();
        function frame() {
          const body = document.querySelector('.study-thread article .study-prose');
          if (body && body.innerText.trim()) {
            const rect = body.getBoundingClientRect();
            if (rect.width && rect.height && rect.top < innerHeight && rect.bottom > 0) {
              window.studyVisibleLatency = performance.now() - start;
              return;
            }
          }
          requestAnimationFrame(frame);
        }
        requestAnimationFrame(frame);
      }, { capture: true, once: true });
    });
    await send.click();
    await page.waitForFunction(() => typeof window.studyVisibleLatency === 'number', null, { timeout: 150000 });
    record.ttft_ms = Math.round(await page.evaluate(() => window.studyVisibleLatency));
    await page.waitForFunction(() => [...document.querySelectorAll('.study-thread article')].some(
      article => article.querySelector('.study-prose') && article.querySelector('h4')?.innerText.includes('已保存')
    ), null, { timeout: 150000 });
    const detail = await page.request.get(route + '/' + id, { headers });
    const reply = (await detail.json()).messages.find(m => m.role === 'assistant');
    record.status = reply.status;
    record.model = reply.model;
    record.tier = reply.trace?.tier;
    record.characters = reply.content.length;
    if (reply.status !== 'completed' || !reply.content.includes('面试')) throw Error('Answer did not complete the sample task');
  } catch (error) {
    record.failure = error.name;
    throw error;
  } finally {
    fs.writeFileSync(out, JSON.stringify(record, null, 2) + '\n');
    await browser.close();
  }
}

run().catch(error => { console.error(error.message); process.exitCode = 1; });
