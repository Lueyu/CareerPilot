import {Component, type ReactNode} from 'react';
import StudyMarkdown from './StudyMarkdown';
import './studyRich.css';
import StudyArtifact, {type Artifact,type Evidence} from './StudyArtifacts';

class LocalFallback extends Component<{children:ReactNode; fallback:ReactNode},{failed:boolean; retry:number}>{
 state={failed:false,retry:0}; static getDerivedStateFromError(){return {failed:true};}
 render(){return this.state.failed?<div>{this.props.fallback}<button onClick={()=>this.setState(s=>({failed:false,retry:s.retry+1}))}>重新渲染</button></div>:this.props.children;}
}
export default function StudyText({text,blocks=[],sources=[],streaming=false}:{text:string;blocks?:unknown[]|null;sources?:Evidence[]|null;streaming?:boolean}){
 const fence=streaming?/(?:^|\n)```study-blocks\s*(?:\n|$)/.exec(text):null;
 const displayed=fence?text.slice(0,fence.index):text;
 // 流式期间用纯文本渲染, 避免每帧整段 markdown 重解析(卡顿主因); 完成后一次性渲染富文本。
 return <div className="study-prose"><LocalFallback fallback={<pre>{displayed}</pre>}>{streaming?<pre className="study-plain-text">{displayed}</pre>:<StudyMarkdown text={displayed} />}</LocalFallback>
 {fence&&<small role="status">正在接收图解数据…</small>}
 {(blocks??[]).map((item,i)=>{const b=item as Artifact;if(!b||typeof b!=='object')return null;const fallback=<p>{String(b.summary??'此产物无法展示。')}</p>;return <LocalFallback key={`${b.id}-${i}`} fallback={fallback}><StudyArtifact b={b} sources={sources??[]}/></LocalFallback>;})}</div>;
}
