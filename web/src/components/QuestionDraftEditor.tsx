import React, { useState } from 'react';
import { AnswerContent, ContextProject, GeneratedItem, QUESTION_LABELS } from '../lib/questionWorkshop';

function TextField({ label, value, onChange, rows = 3 }: {label: string; value: string; onChange: (s: string) => void; rows?: number}) {
  return <label className="field"><span>{label}</span><textarea className="textarea" aria-label={label} rows={rows} value={value} onChange={e => onChange(e.target.value)} /></label>;
}

export default function QuestionDraftEditor({item, skills, projects, busy, onCancel, onSave}: {
  item: GeneratedItem; skills: {key: string; name: string}[]; projects: ContextProject[]; busy: boolean;
  onCancel: () => void; onSave: (item: GeneratedItem) => Promise<unknown>;
}) {
  const [draft, setDraft] = useState<GeneratedItem>(() => structuredClone(item));
  const set = <K extends keyof GeneratedItem>(key: K, value: GeneratedItem[K]) => setDraft(old => ({...old, [key]:value}));
  const answer = <K extends keyof AnswerContent>(key: K, value: AnswerContent[K]) => setDraft(old => ({...old, answer:{...old.answer,[key]:value}}));
  const project = projects.find(p => p.id === draft.project_id);
  const pairFields = (key: 'steps' | 'key_points' | 'followups' | 'terms', label: string, left: string, right: string) => {
    const pairs = draft.answer[key] as unknown as Record<string,string>[];
    return <fieldset style={{border:'1px solid var(--border)',padding:14,marginBottom:14}}><legend>{label}</legend>
      {pairs.map((pair,index) => <div key={index} style={{marginBottom:12}}>
        <TextField label={`${label} ${index+1} · ${left === 'q' ? '问题' : '标题'}`} rows={2} value={pair[left] || ''} onChange={value => answer(key,pairs.map((p,i) => i===index ? {...p,[left]:value} : p) as any)} />
        <TextField label={`${label} ${index+1} · ${right === 'a' ? '回答' : '说明'}`} value={pair[right] || ''} onChange={value => answer(key,pairs.map((p,i) => i===index ? {...p,[right]:value} : p) as any)} />
        <button className="btn btn-ghost btn-sm" type="button" onClick={() => answer(key,pairs.filter((_,i) => i !== index) as any)}>移除此项</button>
      </div>)}
      <button className="btn btn-outline btn-sm" type="button" onClick={() => answer(key,[...pairs,{[left]:'',[right]:''}] as any)}>添加{label}</button>
    </fieldset>;
  };
  return <div>
    <p className="hint">保存会重新校验、排版。原题、项目归属和事实快照保持可追溯；需要补资料时，请更新项目后创建新任务。</p>
    <div className="hint">类型：{QUESTION_LABELS[draft.question_mode]} · 原题：{draft.original_question || '新拟题'}{project ? ` · 项目：${project.name}` : ''}</div>
    <TextField label="题目" rows={3} value={draft.title} onChange={v => set('title',v)} />
    <label className="field"><span>技能</span><select className="select" aria-label="技能" value={draft.skill_key} onChange={e => set('skill_key',e.target.value)}>{skills.map(s => <option key={s.key} value={s.key}>{s.name}</option>)}</select></label>
    {(['tldr','elevator','analogy','how_to_answer'] as const).map((key,index) => <TextField key={key} label={['一句话回答','简洁口述答案','通俗类比','回答组织方式'][index]} value={draft.answer[key] || ''} onChange={v => answer(key,v)} />)}
    {(['examiner','pitfalls'] as const).map((key,index) => <TextField key={key} label={['考察点（每行一项）','常见误区（每行一项）'][index]} value={(draft.answer[key] || []).join('\n')} onChange={v => answer(key,v.split('\n').filter(Boolean))} />)}
    {pairFields('steps','解题步骤','title','detail')}
    {pairFields('key_points','评分要点','title','detail')}
    {pairFields('followups','追问','q','a')}
    {pairFields('terms','术语','term','desc')}
    {draft.question_mode !== 'general' && <>
      <TextField label="结合本人项目的回答" value={draft.project_answer || ''} onChange={v => set('project_answer',v)} />
      <TextField label="假设方案（不能表述为已做经历）" value={draft.hypothetical_answer || ''} onChange={v => set('hypothetical_answer',v)} />
      <p className="hint">引用事实：{draft.fact_refs.join('、') || '未引用'}。{draft.missing_context.join('、')}</p>
    </>}
    <div style={{display:'flex',gap:10}}>
      <button className="btn btn-primary" data-action="save-draft" type="button" disabled={busy || !draft.title.trim()} onClick={() => onSave(draft)}>{busy ? '保存中…' : '保存并重新排版'}</button>
      <button className="btn btn-outline" type="button" disabled={busy} onClick={onCancel}>取消编辑</button>
    </div>
  </div>;
}
