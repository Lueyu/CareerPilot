import React,{useEffect,useRef,useState} from 'react';
import {api} from '../api/client';

type Category='QUESTION_AMBIGUITY'|'RUBRIC_AMBIGUITY'|'REFERENCE_ISSUE'|'OTHER';
interface Feedback {id:number;status:'PENDING_REVIEW';reason:string;category:Category}
const categories:Record<Category,string>={QUESTION_AMBIGUITY:'题意不清楚',RUBRIC_AMBIGUITY:'评分项有歧义',REFERENCE_ISSUE:'参考答案有疑问',OTHER:'其他题目问题'};

export default function QuestionAmbiguityFeedback({examId,questionId}:{examId:number;questionId:number}){
  const [reason,setReason]=useState(''),[category,setCategory]=useState<Category>('QUESTION_AMBIGUITY');
  const [items,setItems]=useState<Feedback[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [historyError,setHistoryError]=useState(''),[reload,setReload]=useState(0);
  const pending=useRef(false),alive=useRef(true),request=useRef<{content:string;key:string}|null>(null),submitted=useRef(false);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  useEffect(()=>{let current=true;setHistoryError('');
    api.get<{items:Feedback[]}>(`/assessment-feedback?exam_id=${examId}&exam_question_id=${questionId}`)
      .then(data=>{if(current && !submitted.current)setItems(data.items || []);})
      .catch(e=>{if(current)setHistoryError(`未能读取已保存反馈：${e instanceof Error?e.message:String(e)}`);});
    return()=>{current=false;};
  },[examId,questionId,reload]);
  const submit=async()=>{
    const clean=reason.trim();if(pending.current || clean.length<5)return;
    pending.current=true;setBusy(true);setError('');
    const content=JSON.stringify([category,clean]);
    if(request.current?.content!==content)request.current={content,key:globalThis.crypto?.randomUUID?.() || `feedback-${Date.now()}-${Math.random().toString(36).slice(2)}`};
    try{
      const saved=await api.post<Feedback>(`/exams/${examId}/questions/${questionId}/feedback`,{category,reason:clean,request_key:request.current.key});
      if(saved.status!=='PENDING_REVIEW' || !saved.id)throw new Error('未收到已保存的反馈记录，请重试核对');
      if(!alive.current)return;
      submitted.current=true;setItems(old=>[saved,...old.filter(item=>item.id!==saved.id)]);setReason('');request.current=null;
    }catch(e){if(alive.current)setError(`反馈未保存：${e instanceof Error?e.message:String(e)}。输入已保留。`);}
    finally{pending.current=false;if(alive.current)setBusy(false);}
  };
  return <section style={{marginTop:16,paddingTop:12,borderTop:'1px dashed var(--border-2)'}} aria-label="题目歧义反馈">
    <div style={{fontSize:13,fontWeight:650}}>这道题或评分标准不清楚？</div>
    <p className="hint">反馈会连同本次题目和评分标准版本保存待审，不会自动改分或修改题库；目前需人工查看待审核记录。</p>
    {historyError && <p role="alert">{historyError} <button className="btn btn-outline btn-sm" onClick={()=>setReload(x=>x+1)}>重读反馈</button></p>}
    {items.map(item=><div key={item.id} role="status" style={{margin:'8px 0'}}><b>已保存 · 待人工审核</b> · {categories[item.category] || '题目反馈'}<p style={{whiteSpace:'pre-wrap',margin:'4px 0'}}>{item.reason}</p></div>)}
    <label>问题类型 <select aria-label="题目反馈类型" value={category} disabled={busy} onChange={e=>setCategory(e.target.value as Category)}>{Object.entries(categories).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
    <textarea aria-label="题目反馈原因" rows={3} maxLength={2000} value={reason} disabled={busy} onChange={e=>setReason(e.target.value)} placeholder="请指出不清楚的具体措辞或评分项（至少 5 个字）" style={{display:'block',width:'100%',margin:'8px 0'}}/>
    {error && <p role="alert">{error}</p>}
    <button className="btn btn-outline btn-sm" disabled={busy || reason.trim().length<5} onClick={submit}>{busy?'正在保存反馈…':'提交题目反馈'}</button>
  </section>;
}
