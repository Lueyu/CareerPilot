import {useEffect,useRef,useState} from 'react';
import {api,ApiError,ExamDetail} from '../api/client';
import {answerPayload,draftKey} from './coreLoop';

export interface DraftAnswer {value:number|string;revision:number;dirty:boolean;conflict?:boolean;serverValue?:number|string;serverRevision?:number}
const valueOf=(q:ExamDetail['questions'][number])=>q.qtype==='mcq'?q.answer?.choice_index??-1:q.answer?.text_answer??'';
const draftStorage=()=>globalThis.localStorage || globalThis.sessionStorage;
const readDraft=(id:number)=>{try{return JSON.parse(draftStorage()?.getItem(draftKey(id)) || globalThis.sessionStorage?.getItem(draftKey(id)) || 'null');}catch{return null;}};
export function clearSavedDraft(id:number){draftStorage()?.removeItem(draftKey(id));globalThis.sessionStorage?.removeItem(draftKey(id));}

export function useExamDraft(exam:ExamDetail|null,enabled:boolean){
  const [entries,setEntries]=useState<DraftAnswer[]>([]);const [saveError,setSaveError]=useState('');const [saving,setSaving]=useState(false);
  const [serverExam,setServerExam]=useState<ExamDetail|null>(null);
  const current=useRef<{exam:ExamDetail;entries:DraftAnswer[]}|null>(null);
  const inFlight=useRef<{examId:number;promise:Promise<void>}|null>(null);const enabledRef=useRef(enabled);enabledRef.current=enabled;
  const mounted=useRef(true);useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  const publish=(next:DraftAnswer[])=>{
    if(!current.current)return;current.current.entries=next;
    try{draftStorage()?.setItem(draftKey(current.current.exam.id),JSON.stringify({version:2,answers:next.map((entry,i)=>({...entry,order_no:current.current!.exam.questions[i].order_no}))}));}
    catch{if(mounted.current)setSaveError('浏览器草稿保存失败，请确认答案已保存到服务器后再离开。');}
    if(mounted.current)setEntries([...next]);
  };
  useEffect(()=>{
    if(!exam){current.current=null;setEntries([]);return;}
    const local=readDraft(exam.id);
    const next=exam.questions.map((q,i)=>{
      const serverValue=valueOf(q),revision=q.answer?.revision??0;
      const saved=local?.version===2?local.answers?.find((a:any)=>a.order_no===q.order_no):Array.isArray(local)?{value:local[i],revision:0,dirty:true}:null;
      if(!saved || !['string','number'].includes(typeof saved.value) || (!saved.dirty && !saved.conflict) || saved.value===serverValue)return {value:serverValue,revision,dirty:false};
      return {value:saved.value,revision:saved.revision??0,dirty:true,conflict:(saved.revision??0)!==revision,serverValue,serverRevision:revision};
    });
    current.current={exam,entries:next};setServerExam(null);setSaveError('');publish(next);
  },[exam?.id]);
  const edit=(index:number,value:number|string)=>{const c=current.current;if(!c || !enabledRef.current)return;const next=c.entries.map((entry,i)=>i===index?{...entry,value,dirty:true}:entry);setSaveError('');publish(next);};
  const reconcile=(fresh:ExamDetail)=>{
    const c=current.current;if(!c || c.exam.id!==fresh.id)return;
    setServerExam(fresh);
    c.exam=fresh;
    publish(c.entries.map((entry,i)=>{
      const q=fresh.questions[i];if(!q)return entry;
      const serverValue=valueOf(q),serverRevision=q.answer?.revision??0;
      if(entry.value===serverValue)return {value:serverValue,revision:serverRevision,dirty:false};
      if(!entry.dirty)return {value:serverValue,revision:serverRevision,dirty:false};
      return {...entry,conflict:true,serverValue,serverRevision};
    }));
  };
  const flush=():Promise<void>=>{
    const initial=current.current;if(!initial)return Promise.resolve();
    if(inFlight.current?.examId===initial.exam.id)return inFlight.current.promise;
    const run=async()=>{
      setSaving(true);setSaveError('');
      try{
        while(current.current===initial){
          const index=initial.entries.findIndex(e=>e.dirty && !e.conflict);if(index<0)return;
          const sent={...initial.entries[index]};
          const result=await api.post<{ok:boolean;revision:number;saved_at:string}>(`/exams/${initial.exam.id}/answers`,{...answerPayload(initial.exam.questions[index],sent.value),expected_revision:sent.revision});
          if(!Number.isInteger(result.revision))throw new Error('保存响应缺少答案版本，请刷新核对后重试');
          if(current.current!==initial || !mounted.current)return;
          publish(initial.entries.map((entry,i)=>i===index?{...entry,revision:result.revision,dirty:entry.value!==sent.value,conflict:false}:entry));
          if(!enabledRef.current)return;
        }
      }catch(e){
        if(current.current===initial && mounted.current){
          if(e instanceof ApiError && e.status===409){
            try{reconcile(await api.get<ExamDetail>(`/exams/${initial.exam.id}`));}catch{/* Keep the local draft and original failure. */}
          }
          setSaveError(`保存未完成：${e instanceof Error?e.message:String(e)}。本地编辑已保留，请核对后重试。`);
        }
        throw e;
      }finally{if(mounted.current && current.current===initial)setSaving(false);}
    };
    const promise=run().finally(()=>{if(inFlight.current?.promise===promise)inFlight.current=null;});inFlight.current={examId:initial.exam.id,promise};return promise;
  };
  useEffect(()=>{
    if(!enabled || saveError || !entries.some(e=>e.dirty && !e.conflict))return;
    const timer=setTimeout(()=>{void flush().catch(()=>{});},800);return()=>clearTimeout(timer);
  },[entries,enabled,saveError]);
  const resolveConflict=(index:number,keepLocal:boolean)=>{
    const c=current.current;if(!c)return;
    publish(c.entries.map((entry,i)=>i===index?{value:keepLocal?entry.value:entry.serverValue??'',revision:entry.serverRevision??entry.revision,dirty:keepLocal,conflict:false}:entry));setSaveError('');
  };
  return {entries,edit,reconcile,flush,resolveConflict,saving,saveError,serverExam,
    waitForSave:()=>inFlight.current?.examId===current.current?.exam.id?inFlight.current?.promise??Promise.resolve():Promise.resolve(),
    getEntries:()=>current.current?.entries??[],
    payload:()=>current.current?.exam.questions.map((q,i)=>({...answerPayload(q,current.current!.entries[i].value),expected_revision:current.current!.entries[i].revision}))??[]};
}
