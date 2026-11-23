import { useRef, useState, type ReactNode } from 'react';
import Markdown from 'react-markdown';
import gfm from 'remark-gfm';
import math from 'remark-math';
import katex from 'rehype-katex';
import highlight from 'rehype-highlight';
import 'katex/dist/katex.min.css';

function Code({ children }: { children?: ReactNode }) {
  const ref = useRef<HTMLPreElement>(null);
  const [notice, setNotice] = useState('');

  async function copy() {
    try {
      await navigator.clipboard.writeText(ref.current?.textContent ?? '');
      setNotice('已复制');
    } catch {
      setNotice('请选中代码复制');
    }
  }

  return <div className="study-code">
    <button type="button" onClick={() => void copy()}>{notice || '复制代码'}</button>
    <pre ref={ref}>{children}</pre>
  </div>;
}

const markdownComponents = {
  pre: Code,
  table: ({ children }: { children?: ReactNode }) =>
    <div className="study-table-scroll"><table>{children}</table></div>,
  img: ({ alt }: { alt?: string }) => <span>{alt || '图片'}</span>,
  a: ({ href, children }: { href?: string; children?: ReactNode }) =>
    href && /^(https?:\/\/|#)/i.test(href)
      ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
      : <span>{children}</span>,
};

export default function StudyMarkdown({ text, allowLinks = true }: { text: string; allowLinks?: boolean }) {
  return <Markdown skipHtml remarkPlugins={[gfm, math]}
    rehypePlugins={[[katex, { trust: false, strict: 'ignore', maxExpand: 100, maxSize: 12 }], [highlight, { detect: false }]]}
    components={markdownComponents} disallowedElements={allowLinks ? undefined : ['a', 'img']} unwrapDisallowed={!allowLinks}>
    {text}
  </Markdown>;
}
