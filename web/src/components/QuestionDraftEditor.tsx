import { useT } from '../i18n';
import React, { useState } from 'react';
import { AnswerContent, ContextProject, GeneratedItem, workshopQuestionLabel, workshopSkillLabel } from '../lib/questionWorkshop';

function TextField({ label, value, onChange, rows = 3 }: {label: string; value: string; onChange: (s: string) => void; rows?: number}) {
  return <label className="field"><span>{label}</span><textarea className="textarea" aria-label={label} rows={rows} value={value} onChange={e => onChange(e.target.value)} /></label>;
}

export default function QuestionDraftEditor({item, skills, projects, busy, onCancel, onSave}: {
  item: GeneratedItem; skills: {key: string; name: string}[]; projects: ContextProject[]; busy: boolean;
  onCancel: () => void; onSave: (item: GeneratedItem) => Promise<unknown>;
}) {
  const t = useT();
  const [draft, setDraft] = useState<GeneratedItem>(() => structuredClone(item));
  const set = <K extends keyof GeneratedItem>(key: K, value: GeneratedItem[K]) => setDraft(old => ({...old, [key]:value}));
  const answer = <K extends keyof AnswerContent>(key: K, value: AnswerContent[K]) => setDraft(old => ({...old, answer:{...old.answer,[key]:value}}));
  const project = projects.find(p => p.id === draft.project_id);
  const pairFields = (key: 'steps' | 'key_points' | 'followups' | 'terms', label: string, left: string, right: string) => {
    const pairs = draft.answer[key] as unknown as Record<string,string>[];
    return <fieldset style={{border:'1px solid var(--border)',padding:14,marginBottom:14}}><legend>{label}</legend>
      {pairs.map((pair,index) => <div key={index} style={{marginBottom:12}}>
        <TextField label={t('bank.draftPairField', { group: label, index: index + 1, field: t(left === 'q' ? 'bank.draftPairQuestion' : 'bank.draftPairTitle') })} rows={2} value={pair[left] || ''} onChange={value => answer(key,pairs.map((p,i) => i===index ? {...p,[left]:value} : p) as any)} />
        <TextField label={t('bank.draftPairField', { group: label, index: index + 1, field: t(right === 'a' ? 'bank.draftPairAnswer' : 'bank.draftPairExplanation') })} value={pair[right] || ''} onChange={value => answer(key,pairs.map((p,i) => i===index ? {...p,[right]:value} : p) as any)} />
        <button className="btn btn-ghost btn-sm" type="button" onClick={() => answer(key,pairs.filter((_,i) => i !== index) as any)}>{t('bank.removeItem')}</button>
      </div>)}
      <button className="btn btn-outline btn-sm" type="button" onClick={() => answer(key,[...pairs,{[left]:'',[right]:''}] as any)}>{t('bank.addDraftPair', { group: label })}</button>
    </fieldset>;
  };
  return <div>
    <p className="hint">{t('bank.draftSaveHelp')}</p>
    <div className="hint">{t('bank.draftQuestionMetadata', { type: workshopQuestionLabel(draft.question_mode), original: draft.original_question || t('bank.newQuestion') })}{project ? t('bank.draftProjectSuffix', { name: project.name }) : ''}</div>
    <TextField label={t('bank.question')} rows={3} value={draft.title} onChange={v => set('title',v)} />
    <label className="field"><span>{t('bank.skill')}</span><select className="select" aria-label={t('bank.skill')} value={draft.skill_key} onChange={e => set('skill_key',e.target.value)}>{skills.map(s => <option key={s.key} value={s.key}>{workshopSkillLabel(s.key, s.name)}</option>)}</select></label>
    {(['tldr','elevator','analogy','how_to_answer'] as const).map((key,index) => <TextField key={key} label={[t('bank.oneSentenceAnswer'),t('bank.spokenAnswer'),t('bank.analogy'),t('bank.answerStructure')][index]} value={draft.answer[key] || ''} onChange={v => answer(key,v)} />)}
    {(['examiner','pitfalls'] as const).map((key,index) => <TextField key={key} label={[t('bank.testedConcepts'),t('bank.commonPitfalls')][index]} value={(draft.answer[key] || []).join('\n')} onChange={v => answer(key,v.split('\n').filter(Boolean))} />)}
    {pairFields('steps',t('bank.solutionSteps'),'title','detail')}
    {pairFields('key_points',t('bank.rubric'),'title','detail')}
    {pairFields('followups',t('bank.followUps'),'q','a')}
    {pairFields('terms',t('bank.terms'),'term','desc')}
    {draft.question_mode !== 'general' && <>
      <TextField label={t('bank.personalProjectAnswer')} value={draft.project_answer || ''} onChange={v => set('project_answer',v)} />
      <TextField label={t('bank.hypotheticalAnswer')} value={draft.hypothetical_answer || ''} onChange={v => set('hypothetical_answer',v)} />
      <p className="hint">{t('bank.citedFacts', { facts: draft.fact_refs.join(t('bank.listSeparator')) || t('bank.noCitations') })}{draft.missing_context.join(t('bank.listSeparator'))}</p>
    </>}
    <div style={{display:'flex',gap:10}}>
      <button className="btn btn-primary" data-action="save-draft" type="button" disabled={busy || !draft.title.trim()} onClick={() => onSave(draft)}>{busy ? t('bank.saving') : t('bank.saveAndFormat')}</button>
      <button className="btn btn-outline" type="button" disabled={busy} onClick={onCancel}>{t('bank.cancelEdit')}</button>
    </div>
  </div>;
}
