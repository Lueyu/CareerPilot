import React from 'react';
import {ExamReportData} from '../api/client';

export function referenceText(html:string){
  return html.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi,'')
    .replace(/<!--([\s\S]*?)-->/g,'').replace(/<br\s*\/?\s*>|<\/(p|div|h[1-6]|li|tr)>/gi,'\n')
    .replace(/<li\b[^>]*>/gi,'• ').replace(/<[^>]*>/g,'')
    .replace(/&(?:amp|lt|gt|quot|apos|nbsp);/g,s=>({'&amp;':'&','&lt;':'<','&gt;':'>','&quot;':'"','&apos;':"'",'&nbsp;':' '}[s]||s))
    .replace(/&#(x[0-9a-f]+|\d+);/gi,(_,s)=>{const n=s[0].toLowerCase()==='x'?parseInt(s.slice(1),16):Number(s);return n>0 && n<=0x10ffff?String.fromCodePoint(n):'';}).trim();
}
const labels:Record<string,string>={core_conclusion:'核心结论',definitions:'必要概念',mechanism:'机制与流程',mechanisms:'机制与流程',example:'具体示例',examples:'具体示例',boundaries:'适用条件与边界',failure_modes:'失败模式',tradeoffs:'方案取舍',verification:'验证方法',project_connection:'工程联系',followups:'可能追问',oral_outline:'60–90 秒口头结构',minimum:'最低合格',good:'良好',excellent:'优秀',required_points:'必答核心点',bonus_points:'加分点',minimum_passing:'最低合格',oral_answer:'口头回答',core_answer:'核心回答',title:'要点',detail:'说明',text:'内容',why:'依据',question:'追问',answer:'参考回答',level:'层级',score:'分值',description:'说明',anchor:'要求',evidence:'回答证据',required_evidence:'所需证据',acceptable_alternatives:'可接受的其他方案',common_misconceptions:'常见误解',critical_errors:'关键错误',score_anchors:'评分锚点',criteria:'评分项',weight:'权重',max_score:'满分'};
function StructuredValue({value}:{value:unknown}){
  if(value==null)return null;
  if(Array.isArray(value))return <ul>{value.map((v,i)=><li key={i}><StructuredValue value={v}/></li>)}</ul>;
  if(typeof value==='object')return <dl>{Object.entries(value).map(([k,v])=><React.Fragment key={k}><dt><b>{labels[k]||k}</b></dt><dd><StructuredValue value={v}/></dd></React.Fragment>)}</dl>;
  return <span style={{whiteSpace:'pre-wrap'}}>{String(value)}</span>;
}
export default function AssessmentEvidence({question,evaluated}:{question:ExamReportData['questions'][number];evaluated:boolean}){
  const feedback=question.answer?.feedback;const rubric=question.frozen?.rubric || {};
  const version=feedback?.rubric_version || rubric.rubric_version;
  const judge=feedback?.judge_version || question.answer?.judge_version;
  const calibration=feedback?.calibration_status || rubric.calibration_status;
  const calibrated=['HUMAN_CALIBRATED','CALIBRATED'].includes(calibration);
  const criteria=feedback?.criterion_scores || [];
  const standard=rubric.reference_standard;
  const criterion=(id:string)=>rubric.criteria?.find((c:any)=>c.criterion_id===id);
  const anchorText=(id:string,anchor:string)=>criterion(id)?.score_anchors?.find((a:any)=>a.anchor_id===anchor)?.description || anchor;
  if(!evaluated)return <section aria-label="评分依据与标准"><p role="status">本题尚未有效评分。原答已保存，后台检查状态不能用于判断回答对错。</p><p className="hint">冻结评分标准版本：{rubric.rubric_version || '未提供可核对的版本'} · 评分器尚未返回本题的有效结果。</p>{rubric.calibration_status && <p className="hint">标准状态：{rubric.calibration_status}；标准已冻结不代表本次评分已成功或已经校准。</p>}<details><summary>查看本次冻结的评分标准</summary>{rubric.criteria || rubric.points?<StructuredValue value={rubric.criteria || rubric.points}/>:<p>当前响应没有可展开的冻结标准，请刷新核对。</p>}</details></section>;
  return <section aria-label="评分依据与标准">
    <p className="hint">评分标准版本：{version || '历史记录未保存'} · 评分器版本：{judge || '历史记录未保存'}</p>
    <p className="hint">{calibrated?'校准状态：已记录人工校准；结论仍只针对本次作答。':calibration==='HUMAN_REVIEWED'?'标准已经人工审核；尚未证明跨题评分已校准，不能当作可靠招聘判断。':'未校准：当前标准尚无已确认的人工校准记录，不能当作可靠招聘判断。'}</p>
    {(feedback?.judge_reasoning || feedback?.advice) && <p><b>本次评分解释：</b>{feedback.judge_reasoning || feedback.advice}</p>}
    {criteria.map(c=><article key={c.criterion_id} style={{borderTop:'1px solid var(--border)',padding:'12px 0'}}>
      <h4>{c.title || criterion(c.criterion_id)?.title || c.criterion_id} · {c.score}/{c.max_score}</h4>
      {c.judge_score!=null && c.judge_score!==c.score && <p>关键错误触发得分上限：原评档 {c.judge_score}，应用预设限制后为 {c.score}。</p>}
      <div><b>你实际回答中的依据：</b>{c.answer_quotes?.length?c.answer_quotes.map((quote,i)=><blockquote key={i} style={{whiteSpace:'pre-wrap'}}>{quote}</blockquote>):<p>未记录可引用的回答片段。</p>}</div>
      {!!c.satisfied_anchors?.length && <p><b>达到的标准：</b>{c.satisfied_anchors.map(a=>anchorText(c.criterion_id,a)).join('；')}</p>}
      {!!c.missing_points?.length && <p><b>还缺什么：</b>{c.missing_points.join('；')}</p>}
      {c.reasoning_summary && <p><b>评分解释：</b>{c.reasoning_summary}</p>}
      {c.why_not_higher && <p><b>为什么没有更高分：</b>{c.why_not_higher}</p>}
    </article>)}
    {feedback?.critical_errors?.map(e=><article key={e.error_id}><h4>影响得分的关键错误：{rubric.critical_errors?.find((x:any)=>x.error_id===e.error_id)?.description || e.error_id}</h4>{e.answer_quotes.map((q,i)=><blockquote key={i}>{q}</blockquote>)}<p>{e.reasoning_summary}</p>{e.applied_cap?.cap_overall!=null && <p>本题总分比例上限：{e.applied_cap.cap_overall}%</p>}{e.applied_cap?.cap_score!=null && <p>对应评分项上限：{e.applied_cap.cap_score}/4</p>}</article>)}
    {!criteria.length && <p className="hint">这份结果没有逐项引用与锚点记录，不能补写成当时已有的评分依据。</p>}
    <details><summary>查看本次冻结的评分标准</summary>{rubric.criteria || rubric.points?<StructuredValue value={rubric.criteria || rubric.points}/>:<p>历史记录未保存可展开的标准。</p>}</details>
    {standard && <details><summary>高分答案结构与口头表达</summary><p className="hint">用于学习和核对评分标准，不保证真实面试通过。</p>
      {standard.alignment_status!=='HUMAN_REVIEWED' && <p className="hint">这份参考尚未人工审核，不是人工金标准；请结合冻结评分项核对。</p>}
      {standard.sections?.map((s:any,i:number)=><article key={i}><h4>{s.title}</h4><p style={{whiteSpace:'pre-wrap'}}>{s.text}</p>{!!s.criterion_ids?.length && <p className="hint">对应评分项：{s.criterion_ids.map((id:string)=>criterion(id)?.title || id).join('、')}</p>}</article>)}
      {standard.levels && <><h4>不同层次的回答要求</h4><StructuredValue value={standard.levels}/></>}
      {standard.oral_60_90_seconds && <><h4>60–90 秒口头回答结构</h4><StructuredValue value={standard.oral_60_90_seconds}/></>}
      {standard.core_points && <><h4>必答核心点</h4><StructuredValue value={standard.core_points}/></>}
      {standard.bonus_points && <><h4>加分点</h4><StructuredValue value={standard.bonus_points}/></>}
      {!!standard.followups?.length && <><h4>可能追问</h4>{standard.followups.map((f:any,i:number)=><article key={i}><p><b>{f.question}</b></p><p>{f.answer}</p></article>)}</>}
      {standard.legacy_reference && <><p>历史参考材料，尚未核对与新评分项的一致性：</p><p style={{whiteSpace:'pre-wrap'}}>{referenceText(standard.legacy_reference)}</p></>}
    </details>}
  </section>;
}
