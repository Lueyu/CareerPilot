import {useEffect,useRef,useState} from 'react';
import {getWorkspace} from '../api/client';

export interface Evidence {label?: string; text?: string; kind?:string;revision?:string;path?:string;line_start?:number;line_end?:number;symbol?:string}
type Diagram={type:'diagram';kind:'architecture'|'flow'|'state'|'sequence';basis:string;nodes:string[];edges:[number,number,string][];sources:number[]};
type Demo={type:'demo';preset:'chunk'|'retry'|'checkpoint'|'queue'};
type Chart={type:'chart';basis:string;unit:string;points:[string,number,number?,(string|number)[]?][]};
export type Artifact={id:string;title:string;summary:string;version_message_id?:number;parent_message_id?:number} & (Diagram|Chart|Demo);
const labels={architecture:'架构图',flow:'流程图',state:'状态图',sequence:'时序图',chunk:'文本分块',checkpoint:'恢复窗口',retry:'重试退避',queue:'并发排队'};
function DiagramView({b}:{b:Diagram}){
  const id=useRef('arr'+Math.random().toString(36).slice(2)).current;
  const seq=b.kind==='sequence', count=b.nodes.length;
  const width=seq?Math.max(400,count*150):590;
  const height=seq?96+b.edges.length*55:72+b.nodes.length*82;
  const x=(n:number)=>seq?(n+.5)*width/count:236;
  const y=(n:number)=>seq?34:36+n*82;
  return <div className="study-graphic-scroll"><svg role="img" aria-label={labels[b.kind]} width={width} height={height} viewBox={`0 0 ${width} ${height}`}><defs><marker id={id} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#3776a5"/></marker></defs>
  {seq&&b.nodes.map((_,i)=><line key={i} x1={x(i)} x2={x(i)} y1="54" y2={height-10} stroke="#a3b3c5" strokeDasharray="5 4"/>)}
  {b.edges.map(([from,to,label],i)=>{ const ly=80+i*55, side=i%2===0?1:-1, curve=side*(85+(i%3)*26), mid=(y(from)+y(to))/2;
    const sx=seq?x(from):x(from)+side*65, ex=seq?x(to):x(to)+side*65;
    const line=seq?(from===to?`M${sx} ${ly} h42 v16 h-42`:`M${sx} ${ly} H${ex}`):(from===to?`M${sx} ${mid-8} c${curve} -42 ${curve} 42 0 16`:`M${sx} ${y(from)} C${sx+curve} ${y(from)} ${ex+curve} ${y(to)} ${ex} ${y(to)}`);
    return <g key={i}><title>{b.nodes[from]} → {b.nodes[to]}：{label}</title><path d={line} stroke="#3776a5" fill="none" markerEnd={`url(#${id})`}/><text x={seq?(sx+ex)/2+(from===to?34:0):sx+curve*.78} y={(seq?ly:mid)-6} className="study-edge-label" textAnchor="middle">{seq?`${i+1}. `:''}{label.length>14?label.slice(0,13)+'…':label}</text></g>})}
  {b.nodes.map((node,i)=><g key={i}><title>{node}</title><rect x={x(i)-65} y={y(i)-17} width="130" height="34" rx={b.kind==='state'?17:7} fill="#e7f2f5" stroke="#6694a0"/><text x={x(i)} y={y(i)+5} textAnchor="middle" fill="#173b46" fontSize="14">{node.length>9?node.slice(0,8)+'…':node}</text></g>)}</svg></div>;
}
const sample='将面试资料切成片段，给每段保留来源。检索时找出相关片段，再让模型结合上下文作答。片段过短可能割裂信息，片段过长则会带入无关内容。';
type Controls = {n:number;flag:boolean};
function DemoView({preset,controls,onChange}:{preset:Demo['preset'];controls:Controls;onChange:(v:Controls)=>void}){
 const {n,flag}=controls;
 const len=n*4, batches=Math.ceil(12/n);
 return <div className="study-simulator"><label>{preset==='chunk'?'每段字符数':preset==='retry'?'失败次数':preset==='queue'?'并发工作数':'故障位置'}：{preset==='chunk'?len:preset==='checkpoint'?['副作用前','副作用后、写检查点前','检查点后'][Math.min(n,3)-1]:n}<input type="range" min="1" max={preset==='checkpoint'?3:6} value={n} onChange={e=>onChange({n:Number(e.target.value),flag})}/></label>
 {preset==='checkpoint'&&<label><input type="checkbox" checked={flag} onChange={e=>onChange({n,flag:e.target.checked})}/>外部系统使用幂等键去重</label>}
 {preset==='retry'&&<label><input type="checkbox" checked={flag} onChange={e=>onChange({n,flag:e.target.checked})}/>使用指数退避</label>}
 <div aria-live="polite">{preset==='chunk'?<><p>固定字符分块（无重叠），共 {Math.ceil(sample.length/len)} 段。</p><ol>{Array.from({length:Math.ceil(sample.length/len)},(_,i)=><li key={i}>{sample.slice(i*len,(i+1)*len)}</li>)}</ol></>:preset==='queue'?<p>12 个等长、耗时 1 秒的任务，需要 {batches} 批；最后一批在第 {batches} 秒完成。此模拟忽略调度开销。</p>:preset==='retry'?<p>重试等待：{Array.from({length:n},(_,i)=>flag?2**i:1).join(' → ')} 秒。累计等待 {flag?2**n-1:n} 秒，随后第 {n+1} 次调用成功。</p>:<p>{n===1?'恢复时未完成的步骤重新执行，外部副作用发生一次。':n===2?(flag?'恢复时再次调用外部系统；相同幂等键命中之前的结果，副作用仍只有一次。':'检查点尚未记录完成。恢复时重跑这一步，外部副作用发生两次。'):'检查点已经记录完成，恢复后从下一步继续，副作用发生一次。'}</p>}</div></div>;
}
function ChartView({b}:{b:Chart}){const max=Math.max(1,...b.points.map(p=>Math.abs(p[1])));return <dl className="study-chart">{b.points.map(([k,v],i)=><div key={i}><dt>{k}</dt><dd><i style={{width:`${Math.abs(v)/max*90}%`}}/>{v} {b.unit}</dd></div>)}</dl>}
export default function StudyArtifact({b,sources}:{b:Artifact;sources:Evidence[]}){
 const ref=useRef<HTMLDialogElement>(null); const [zoom,setZoom]=useState(false);
 const controlKey=b.version_message_id && b.type==='demo'
   ? JSON.stringify(['coach-demo',getWorkspace(),b.version_message_id,b.id,b.preset]) : '';
 const [controls,setControls]=useState<Controls>(()=>{
   if(controlKey)try {
     const val=JSON.parse(localStorage.getItem(controlKey)||'null');
     const maximum=b.type==='demo'&&b.preset==='checkpoint'?3:6;
     if(val&&Number.isInteger(val.n)&&val.n>=1&&val.n<=maximum&&typeof val.flag==='boolean')return val;
   }catch { /* Local storage may be disabled. */ }
   return {n:3,flag:false};
 });
 const updateControls=(next:Controls)=>{setControls(next);if(controlKey)try{localStorage.setItem(controlKey,JSON.stringify(next))}catch{}};
 useEffect(()=>{if(zoom&&!ref.current?.open)ref.current?.showModal();},[zoom]);
 const refs=b.type==='diagram'?b.sources:b.type==='chart'?[...new Set(b.points.flatMap(p=>p[2]===undefined?[]:[p[2]]))]:[];
 const view=()=>b.type==='diagram'?<DiagramView b={b}/>:b.type==='chart'?<ChartView b={b}/>:<DemoView preset={b.preset} controls={controls} onChange={updateControls}/>;
 const category=b.type==='demo'?'教学模拟':b.basis==='project'?'项目代码依据':b.basis==='proposal'?'建议设计':b.basis==='observed'?'来源数据':'教学示例（模拟）';
 return <section className="study-artifact"><header><div><small>{category} · {b.type==='diagram'?labels[b.kind]:b.type==='demo'?labels[b.preset]:'数据图'}</small><h4>{b.title}</h4></div><button type="button" onClick={()=>setZoom(true)}>放大</button></header>{view()}<p>{b.summary}</p>{b.version_message_id&&<small>产物版本：消息 #{b.version_message_id}{b.parent_message_id&&<> · 前序消息 <a href={"#study-message-"+b.parent_message_id}>#{b.parent_message_id}</a></>}</small>}
 {refs.map(i=>sources[i]&&<details key={i}><summary>依据：{sources[i].label??`材料 ${i+1}`}</summary><p>{sources[i].path}{sources[i].line_start?`:${sources[i].line_start}–${sources[i].line_end??sources[i].line_start}`:""} {sources[i].symbol}</p><small>版本：{sources[i].revision||"未验证"}</small><pre>{sources[i].text}</pre></details>)}
 <details><summary>查看结构化源码</summary><pre>{JSON.stringify(b,null,2)}</pre></details>
 {zoom&&<dialog ref={ref} onClose={()=>setZoom(false)} aria-label={b.title}><button type="button" onClick={()=>ref.current?.close()}>关闭</button><h3>{b.title}</h3>{view()}<p>{b.summary}</p>{b.version_message_id&&<small>产物版本：消息 #{b.version_message_id}{b.parent_message_id&&<> · 前序消息 <a href={"#study-message-"+b.parent_message_id}>#{b.parent_message_id}</a></>}</small>}</dialog>}</section>;
}
