import React, {useState} from 'react';
import {api} from '../api/client';
import {messageOf} from '../lib/questionWorkshop';
import {Modal} from './ui';

export default function BankQuestionEditor({question,topics,onClose,onSaved}: {
  question:{id:string;title:string;topic:string;difficulty:number;company:string};
  topics:{code:string;name:string}[];onClose:()=>void;onSaved:()=>void;
}) {
  const [title,setTitle]=useState(question.title);
  const [topic,setTopic]=useState(question.topic);
  const [difficulty,setDifficulty]=useState(question.difficulty);
  const [company,setCompany]=useState(question.company);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const save=async()=>{
    setBusy(true);setError('');
    try{await api.patch(`/bank/questions/${question.id}`,{title:title.trim(),topic,difficulty,company});onSaved();}
    catch(e){setError(messageOf(e));}finally{setBusy(false);}
  };
  return <Modal title="编辑题目信息" onClose={()=>{if(!busy)onClose();}} footer={<><button className="btn btn-outline" disabled={busy} onClick={onClose}>取消</button><button className="btn btn-primary" data-action="save-question" disabled={busy || !title.trim()} onClick={save}>{busy?'保存中…':'保存'}</button></>}>
    <label className="field"><span>题目</span><textarea className="textarea" aria-label="题目" rows={4} value={title} onChange={e=>setTitle(e.target.value)}/></label>
    <label className="field"><span>主题</span><select className="select" value={topic} onChange={e=>setTopic(e.target.value)}>{topics.map(t=><option key={t.code} value={t.code}>{t.name}</option>)}</select></label>
    <label className="field"><span>难度</span><select className="select" value={difficulty} onChange={e=>setDifficulty(Number(e.target.value))}>{[1,2,3,4,5].map(n=><option key={n} value={n}>{n}</option>)}</select></label>
    <label className="field"><span>来源标注</span><input className="inp" value={company} onChange={e=>setCompany(e.target.value)}/></label>
    <p className="hint">此处保存题目基本信息。生成任务的答案、追问与评分要点可在工作台入库前编辑。</p>
    {(title.trim()!==question.title || topic!==question.topic) && <p role="status" className="demo-banner">题意或主题已改变，需要重新核对参考答案、评分项与能力映射。修改题面本身不会证明旧评分标准仍适用，也不会重写过去答卷的冻结版本。</p>}
    {error && <p role="alert" className="demo-banner demo-banner-err">{error}</p>}
  </Modal>;
}
