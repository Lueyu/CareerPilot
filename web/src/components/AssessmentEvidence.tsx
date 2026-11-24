import { useT } from '../i18n';
import React from 'react';
import {ExamReportData} from '../api/client';

export function referenceText(html:string){
  return html.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi,'')
    .replace(/<!--([\s\S]*?)-->/g,'').replace(/<br\s*\/?\s*>|<\/(p|div|h[1-6]|li|tr)>/gi,'\n')
    .replace(/<li\b[^>]*>/gi,'• ').replace(/<[^>]*>/g,'')
    .replace(/&(?:amp|lt|gt|quot|apos|nbsp);/g,s=>({'&amp;':'&','&lt;':'<','&gt;':'>','&quot;':'"','&apos;':"'",'&nbsp;':' '}[s]||s))
    .replace(/&#(x[0-9a-f]+|\d+);/gi,(_,s)=>{const n=s[0].toLowerCase()==='x'?parseInt(s.slice(1),16):Number(s);return n>0 && n<=0x10ffff?String.fromCodePoint(n):'';}).trim();
}
const labels:Record<string,string>={core_conclusion:"study.coreConclusion",definitions:"study.essentialConcepts",mechanism:"study.mechanismAndFlow",mechanisms:"study.mechanismAndFlow",example:"study.concreteExample",examples:"study.concreteExample",boundaries:"study.applicability",failure_modes:"study.failureModes",tradeoffs:"study.tradeoffs",verification:"study.verificationMethods",project_connection:"study.engineeringConnections",followups:"study.possibleFollowUps",oral_outline:"study.shortSpokenStructure",minimum:"study.minimumPassing",good:"study.good",excellent:"study.excellent",required_points:"study.essentialAnswerPoints",bonus_points:"study.bonusPoints",minimum_passing:"study.minimumPassing",oral_answer:"study.spokenAnswer",core_answer:"study.coreAnswer",title:"study.keyPoint",detail:"study.explanation",text:"study.content",why:"study.evidence",question:"study.followUp",answer:"study.referenceResponse",level:"study.level",score:"study.pointValue",description:"study.explanation",anchor:"study.requirements",evidence:"study.answerEvidence",required_evidence:"study.requiredEvidence",acceptable_alternatives:"study.acceptableAlternatives",common_misconceptions:"study.commonMisconceptions",critical_errors:"study.criticalError",score_anchors:"study.scoringAnchors",criteria:"study.criterion",weight:"study.weight",max_score:"study.maximumScore"};
function StructuredValue({value}:{value:unknown}){
  const t = useT();
  if(value==null)return null;
  if(Array.isArray(value))return <ul>{value.map((v,i)=><li key={i}><StructuredValue value={v}/></li>)}</ul>;
  if(typeof value==='object')return <dl>{Object.entries(value).map(([k,v])=><React.Fragment key={k}><dt><b>{Object.prototype.hasOwnProperty.call(labels, k) ? t(labels[k]) : k}</b></dt><dd><StructuredValue value={v}/></dd></React.Fragment>)}</dl>;
  return <span style={{whiteSpace:'pre-wrap'}}>{String(value)}</span>;
}
export default function AssessmentEvidence({question,evaluated}:{question:ExamReportData['questions'][number];evaluated:boolean}){
  const t = useT();

  const feedback=question.answer?.feedback;const rubric=question.frozen?.rubric || {};
  const version=feedback?.rubric_version || rubric.rubric_version;
  const judge=feedback?.judge_version || question.answer?.judge_version;
  const calibration=feedback?.calibration_status || rubric.calibration_status;
  const calibrated=['HUMAN_CALIBRATED','CALIBRATED'].includes(calibration);
  const criteria=feedback?.criterion_scores || [];
  const standard=rubric.reference_standard;
  const criterion=(id:string)=>rubric.criteria?.find((c:any)=>c.criterion_id===id);
  const anchorText=(id:string,anchor:string)=>criterion(id)?.score_anchors?.find((a:any)=>a.anchor_id===anchor)?.description || anchor;
  if(!evaluated)return <section aria-label={t("study.scoringEvidenceAndRubric")}><p role="status">{t("study.scorePendingEvidence")}</p><p className="hint">{t("study.frozenRubricWithoutScore", { rubricVersion: rubric.rubric_version || t("study.rubricVersionUnavailable") })}</p>{rubric.calibration_status && <p className="hint">{t("study.frozenRubricStatus", { calibrationStatus: rubric.calibration_status })}</p>}<details><summary>{t("study.viewFrozenRubric")}</summary>{rubric.criteria || rubric.points?<StructuredValue value={rubric.criteria || rubric.points}/>:<p>{t("study.frozenRubricUnavailable")}</p>}</details></section>;
  return <section aria-label={t("study.scoringEvidenceAndRubric")}>
    <p className="hint">{t("study.scoringVersions", { rubricVersion: version || t("study.versionMissingFromHistory"), scorerVersion: judge || t("study.versionMissingFromHistory") })}</p>
    <p className="hint">{calibrated?t("study.manuallyCalibrated"):calibration==='HUMAN_REVIEWED'?t("study.humanReviewedCalibration"):t("study.notCalibrated")}</p>
    {(feedback?.judge_reasoning || feedback?.advice) && <p><b>{t("study.scoreRationale")}</b>{feedback.judge_reasoning || feedback.advice}</p>}
    {criteria.map(c=><article key={c.criterion_id} style={{borderTop:'1px solid var(--border)',padding:'12px 0'}}>
      <h4>{c.title || criterion(c.criterion_id)?.title || c.criterion_id} · {c.score}/{c.max_score}</h4>
      {c.judge_score!=null && c.judge_score!==c.score && <p>{t("study.criticalErrorCap", { judgeScore: c.judge_score, score: c.score })}</p>}
      <div><b>{t("study.evidenceFromYourAnswer")}</b>{c.answer_quotes?.length?c.answer_quotes.map((quote,i)=><blockquote key={i} style={{whiteSpace:'pre-wrap'}}>{quote}</blockquote>):<p>{t("study.noAnswerQuotes")}</p>}</div>
      {!!c.satisfied_anchors?.length && <p><b>{t("study.standardsMet")}</b>{c.satisfied_anchors.map(a=>anchorText(c.criterion_id,a)).join(t("study.clauseSeparator"))}</p>}
      {!!c.missing_points?.length && <p><b>{t("study.whatIsMissing")}</b>{c.missing_points.join(t("study.clauseSeparator"))}</p>}
      {c.reasoning_summary && <p><b>{t("study.scoringExplanation")}</b>{c.reasoning_summary}</p>}
      {c.why_not_higher && <p><b>{t("study.whyScoreWasNotHigher")}</b>{c.why_not_higher}</p>}
    </article>)}
    {feedback?.critical_errors?.map(e=><article key={e.error_id}><h4>{t("study.criticalErrorDescription", { description: rubric.critical_errors?.find((x:any)=>x.error_id===e.error_id)?.description || e.error_id })}</h4>{e.answer_quotes.map((q,i)=><blockquote key={i}>{q}</blockquote>)}<p>{e.reasoning_summary}</p>{e.applied_cap?.cap_overall!=null && <p>{t("study.overallScoreCap", { capOverall: e.applied_cap.cap_overall })}</p>}{e.applied_cap?.cap_score!=null && <p>{t("study.criterionScoreCap", { capScore: e.applied_cap.cap_score })}</p>}</article>)}
    {!criteria.length && <p className="hint">{t("study.evidenceNotCaptured")}</p>}
    <details><summary>{t("study.viewFrozenRubric")}</summary>{rubric.criteria || rubric.points?<StructuredValue value={rubric.criteria || rubric.points}/>:<p>{t("study.noHistoricalRubricDetails")}</p>}</details>
    {standard && <details><summary>{t("study.highScoringAnswerStructure")}</summary><p className="hint">{t("study.practiceRubricDisclaimer")}</p>
      {standard.alignment_status!=='HUMAN_REVIEWED' && <p className="hint">{t("study.referenceNotHumanReviewed")}</p>}
      {standard.sections?.map((s:any,i:number)=><article key={i}><h4>{s.title}</h4><p style={{whiteSpace:'pre-wrap'}}>{s.text}</p>{!!s.criterion_ids?.length && <p className="hint">{t("study.relatedCriteria", { criteria: s.criterion_ids.map((id:string)=>criterion(id)?.title || id).join(t("study.listSeparator")) })}</p>}</article>)}
      {standard.levels && <><h4>{t("study.answerRequirementsByLevel")}</h4><StructuredValue value={standard.levels}/></>}
      {standard.oral_60_90_seconds && <><h4>{t("study.spokenAnswerOutline")}</h4><StructuredValue value={standard.oral_60_90_seconds}/></>}
      {standard.core_points && <><h4>{t("study.essentialAnswerPoints")}</h4><StructuredValue value={standard.core_points}/></>}
      {standard.bonus_points && <><h4>{t("study.bonusPoints")}</h4><StructuredValue value={standard.bonus_points}/></>}
      {!!standard.followups?.length && <><h4>{t("study.possibleFollowUps")}</h4>{standard.followups.map((f:any,i:number)=><article key={i}><p><b>{f.question}</b></p><p>{f.answer}</p></article>)}</>}
      {standard.legacy_reference && <><p>{t("study.historicalReferenceUnverified")}</p><p style={{whiteSpace:'pre-wrap'}}>{referenceText(standard.legacy_reference)}</p></>}
    </details>}
  </section>;
}
