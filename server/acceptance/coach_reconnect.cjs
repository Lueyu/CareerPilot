// A single low-cost real turn in the existing isolated browser acceptance workspace.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'../../.audit');
 const evidence=JSON.parse(fs.readFileSync(path.join(root,'coach-review-live/evidence.json')));
 assert(evidence.workspace.startsWith('accept-coach-'));
 const b=await chromium.launch({channel:'msedge'});const c=await b.newContext();
 const p=await c.newPage();p.setDefaultTimeout(85000);
 const result={workspace:evidence.workspace,session:evidence.session.id};
 try{
  await c.addInitScript(slug=>localStorage.setItem('cp-workspace',slug),evidence.workspace);
  const base='http://127.0.0.1:8002/api/v1/study/sessions/'+result.session;
  const opening=p.waitForRequest(r=>r.url()===base);
  await p.goto('http://127.0.0.1:5173/study/chat/'+result.session);
  const h=await(await opening).allHeaders();assert.equal(h['x-workspace'],evidence.workspace);
  const auth={'x-api-token':h['x-api-token'],'x-workspace':evidence.workspace};
  await p.getByLabel('继续问').fill('断网恢复验收。用一句话解释什么是幂等键。');
  const ack=p.waitForResponse(r=>r.url()===base+'/messages'&&r.request().method()==='POST');
  await p.getByRole('button',{name:'发送',exact:true}).click();const response=await ack;assert(response.ok());
  const ticket=await response.json();result.ticket=ticket;
  let failed=0;p.on('requestfailed',r=>{if(r.url().startsWith(base))failed++});
  await c.setOffline(true);await p.waitForTimeout(3200);
  await c.setOffline(false);result.failed_polls=failed;assert(failed>0);
  await p.reload();await p.getByLabel('继续问').waitFor();
  const until=Date.now()+130000;
  while(Date.now()<until){
   const res=await p.request.get(base,{headers:auth});const rows=(await res.json()).messages;
   const m=rows.find(x=>x.id===ticket.assistant_message_id);
   if(m&&m.status!=='generating'){assert.equal(m.status,'completed');assert.equal(rows.filter(x=>x.request_id===m.request_id).length,2);
    result.id=m.id;result.trace=m.trace;result.count=2;result.restored=!!m.content;break}
   await p.waitForTimeout(500);
  }
  assert(result.restored);await p.reload();await p.getByLabel('继续问').waitFor();
  const match=p.locator('#study-message-'+result.id);await match.waitFor();result.browser_message=await match.isVisible();
  assert(result.browser_message);result.ok=true;
 }catch(e){result.error=e.message;process.exitCode=1}finally{fs.writeFileSync(path.join(root,'coach-live/reconnect.json'),JSON.stringify(result,null,2));await b.close()}
})().catch(e=>{console.error(e.message);process.exitCode=1});
