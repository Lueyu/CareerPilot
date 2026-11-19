// Run against the local personal app. Writes a new clearly named acceptance conversation.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const output = process.env.COACH_EVIDENCE_DIR || path.resolve(__dirname, '../../.audit/coach-browser');
fs.mkdirSync(output, { recursive: true });
const base = process.env.COACH_WEB || 'http://127.0.0.1:5173';
const report = { base, started: new Date().toISOString(), turns: [] };
async function run() {
  const browser = await chromium.launch({ channel: 'msedge' });
  const page = await browser.newPage({ viewport: {width:1366,height:820} });
  page.setDefaultTimeout(180000);
  try {
    const initial = page.waitForRequest(r => r.url().includes('/api/v1/study/sessions'));
    await page.goto(base+'/study/chat');
    const req = await initial;
    const headers = await req.allHeaders();
    const api = req.url().split('/api/v1/')[0]+'/api/v1';
    const auth = {'x-workspace':headers['x-workspace'],'x-api-token':headers['x-api-token']};
    report.api = api; report.workspace = auth['x-workspace'];
    await page.getByLabel('学习问题').fill('学习教练验收：什么是 RAG？用两句话解释。');
    const created = page.waitForResponse(r=>r.url().endsWith('/study/sessions') && r.request().method()==='POST');
    await page.getByRole('button',{name:'开始学习'}).click();
    const sid = (await (await created).json()).id;
    report.session = sid;
    const url = api+'/study/sessions/'+sid;
    const store = () => fs.writeFileSync(path.join(output,'result.json'), JSON.stringify(report,null,2));
    async function waitTurn(index) {
      const until = Date.now()+170000;
      while (Date.now()<until) {
        const res = await page.request.get(url, {headers:auth});
        assert(res.ok());
        const m = (await res.json()).messages.filter(m=>m.role==='assistant')[index];
        if (m && m.status !== 'generating') {
          report.turns.push({id:m.id,status:m.status,trace:m.trace,blocks:m.blocks?.map(b=>b.type),
            sources:m.sources.filter(s=>s.path).map(s=>({path:s.path,symbol:s.symbol,revision:s.revision,line_start:s.line_start})),
            excerpt:m.content.slice(0,430),error:m.error});store();
          assert.equal(m.status,'completed',JSON.stringify(m.trace));return m;
        }
        await page.waitForTimeout(650);
      }
      throw Error('generation timed out');
    }
    const simple = await waitTurn(0);
    assert.equal(simple.trace.tier,'flash');
    await page.waitForURL('**/study/chat/**');
    const composer = page.getByLabel('继续问');
    async function send(text,index) {
      await composer.fill(text);
      await page.getByRole('button',{name:'发送',exact:true}).click();
      return waitTurn(index);
    }
    const deep = await send('深入解释 checkpoint 恢复、幂等和外部副作用的失败窗口。画一张时序图，然后用三句话解释。',1);
    assert.equal(deep.trace.tier,'strong');
    assert(/幂等|副作用|失败窗口/.test(deep.content));
    assert(deep.blocks.some(b=>b.type==='diagram'));
    const followup = await send('这一步为什么会重复？只回答刚才的失败窗口。',2);
    assert.equal(followup.trace.tier,'strong');
    const linked=page.getByLabel('关联项目');
    const linkedSaved=page.waitForResponse(r=>r.url()===url && r.request().method()==='PATCH');
    await linked.selectOption({label:'CareerPilot'});
    assert((await linkedSaved).ok());
    const code = await send('结合 CareerPilot 项目讲解 study_chat 的 generate_reply 如何选择模型，引用一个实际文件、函数与版本。只讲已经查到的部分。',3);
    assert(code.sources.some(s=>s.path && s.revision));
    const demo=await send('做一个可以调整故障位置的 checkpoint 交互演示，并说明这是教学模拟。',4);
    assert(demo.blocks?.some(b=>b.type==='demo'));
    await page.reload();await composer.waitFor();
    await page.getByText(demo.blocks.find(b=>b.type==='demo').title,{exact:true}).first().waitFor();
    await page.screenshot({path:path.join(output,'desktop.png')});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(output,'mobile.png')});
    report.result='passed';store();
  } catch(e) {
    report.error=e.message.slice(0,900);
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(report,null,2));
    process.exitCode=1;
  } finally { await browser.close(); }
}
run().catch(e=>{console.error(e.message);process.exitCode=1});
