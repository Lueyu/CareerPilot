import { useT } from '../i18n';
import React, {useState} from 'react';
import {api} from '../api/client';
import { bankSourcePresentation, PRIVATE_COMPANY_FALLBACK } from '../lib/bankSource';
import {messageOf} from '../lib/questionWorkshop';
import {Modal} from './ui';

export default function BankQuestionEditor({question,topics,onClose,onSaved}: {
  question:{id:string;title:string;topic:string;difficulty:number;company:string;source_type:string};
  topics:{code:string;name:string}[];onClose:()=>void;onSaved:()=>void;
}) {
  const t = useT();
  const [title,setTitle]=useState(question.title);
  const [topic,setTopic]=useState(question.topic);
  const [difficulty,setDifficulty]=useState(question.difficulty);
  const [company,setCompany]=useState<string | null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const save=async()=>{
    setBusy(true);setError('');
    try{await api.patch(`/bank/questions/${question.id}`,{title:title.trim(),topic,difficulty,company:company ?? (question.company.trim() || PRIVATE_COMPANY_FALLBACK)});onSaved();}
    catch(e){setError(messageOf(e));}finally{setBusy(false);}
  };
  return <Modal title={t('bank.editQuestionInfo')} onClose={()=>{if(!busy)onClose();}} footer={<><button className="btn btn-outline" disabled={busy} onClick={onClose}>{t('bank.cancel')}</button><button className="btn btn-primary" data-action="save-question" disabled={busy || !title.trim()} onClick={save}>{busy?t('bank.saving'):t('bank.save')}</button></>}>
    <label className="field"><span>{t('bank.question')}</span><textarea className="textarea" aria-label={t('bank.question')} rows={4} value={title} onChange={e=>setTitle(e.target.value)}/></label>
    <label className="field"><span>{t('bank.topic')}</span><select className="select" value={topic} onChange={e=>setTopic(e.target.value)}>{topics.map(t=><option key={t.code} value={t.code}>{t.name}</option>)}</select></label>
    <label className="field"><span>{t('bank.difficulty')}</span><select className="select" value={difficulty} onChange={e=>setDifficulty(Number(e.target.value))}>{[1,2,3,4,5].map(n=><option key={n} value={n}>{n}</option>)}</select></label>
    <label className="field"><span>{t('bank.sourceNote')}</span><input className="inp" value={company ?? bankSourcePresentation(question).company} onChange={e=>setCompany(e.target.value)}/></label>
    <p className="hint">{t('bank.editQuestionHelp')}</p>
    {(title.trim()!==question.title || topic!==question.topic) && <p role="status" className="demo-banner">{t('bank.editQuestionRecheck')}</p>}
    {error && <p role="alert" className="demo-banner demo-banner-err">{error}</p>}
  </Modal>;
}
