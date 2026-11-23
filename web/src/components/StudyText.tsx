import {Component, useRef, useState, type ReactNode} from 'react';
import Markdown from 'react-markdown';
import gfm from 'remark-gfm';
import math from 'remark-math';
import katex from 'rehype-katex';
import highlight from 'rehype-highlight';
import 'katex/dist/katex.min.css';
import './studyRich.css';
import StudyArtifact, {type Artifact,type Evidence} from './StudyArtifacts';

class LocalFallback extends Component<{children:ReactNode; fallback:ReactNode},{failed:boolean; retry:number}>{
 state={failed:false,retry:0}; static getDerivedStateFromError(){return {failed:true};}
 render(){return this.state.failed?<div>{this.props.fallback}<button onClick={()=>this.setState(s=>({failed:false,retry:s.retry+1}))}>重新渲染</button></div>:this.props.children;}
}
function Code({children}:{children?:ReactNode}){const ref=useRef<HTMLPreElement>(null),[notice,setNotice]=useState('');return <div className="study-code"><button type="button" onClick={async()=>{try{await navigator.clipboard.writeText(ref.current?.textContent??'');setNotice('已复制');}catch{setNotice('请选中代码复制');}}}>{notice||'复制代码'}</button><pre ref={ref}>{children}</pre></div>}
function sourceLink(href:string|undefined){return href && /^(https?:\/\/|#)/i.test(href)?href:undefined;}
const markdownComponents={pre:Code,table:({children}:{children?:ReactNode})=><div className="study-table-scroll"><table>{children}</table></div>,img:({alt}:{alt?:string})=><span>{alt||'图片'}</span>,a:({href,children}:{href?:string;children?:ReactNode})=>sourceLink(href)?<a href={sourceLink(href)} target="_blank" rel="noopener noreferrer">{children}</a>:<span>{children}</span>};
export default function StudyText({text,blocks=[],sources=[],streaming=false}:{text:string;blocks?:unknown[]|null;sources?:Evidence[]|null;streaming?:boolean}){
 const fence=streaming?/(?:^|\n)```study-blocks\s*(?:\n|$)/.exec(text):null;
 const displayed=fence?text.slice(0,fence.index):text;
 // 流式期间用纯文本渲染, 避免每帧整段 markdown 重解析(卡顿主因); 完成后一次性渲染富文本。
 return <div className="study-prose"><LocalFallback fallback={<pre>{displayed}</pre>}>{streaming?<pre className="study-plain-text">{displayed}</pre>:<Markdown skipHtml remarkPlugins={[gfm,math]} rehypePlugins={[[katex,{trust:false,strict:'ignore',maxExpand:100,maxSize:12}],[highlight,{detect:false}]]} components={markdownComponents}>{displayed}</Markdown>}</LocalFallback>
 {fence&&<small role="status">正在接收图解数据…</small>}
 {(blocks??[]).map((item,i)=>{const b=item as Artifact;if(!b||typeof b!=='object')return null;const fallback=<p>{String(b.summary??'此产物无法展示。')}</p>;return <LocalFallback key={`${b.id}-${i}`} fallback={fallback}><StudyArtifact b={b} sources={sources??[]}/></LocalFallback>;})}</div>;
}
