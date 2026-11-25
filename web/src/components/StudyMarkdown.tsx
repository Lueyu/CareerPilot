import { useT } from '../i18n';
import { useRef, useState, type ReactNode } from 'react';
import Markdown from 'react-markdown';
import gfm from 'remark-gfm';
import math from 'remark-math';
import katex from 'rehype-katex';
import highlight from 'rehype-highlight';
import 'katex/dist/katex.min.css';

function Code({ children }: { children?: ReactNode }) {
  const t = useT();

  const ref = useRef<HTMLPreElement>(null);
  const [notice, setNotice] = useState('');

  async function copy() {
    try {
      await navigator.clipboard.writeText(ref.current?.textContent ?? '');
      setNotice("study.copied");
    } catch {
      setNotice("study.selectCodeToCopy");
    }
  }

  return <div className="study-code">
    <button type="button" onClick={() => void copy()}>{t(notice || "study.copyCode")}</button>
    <pre ref={ref}>{children}</pre>
  </div>;
}

function ImagePlaceholder({ alt }: { alt?: string }) {
  const t = useT();
  return <span>{alt || t("study.image")}</span>;
}

const markdownComponents = {
  pre: Code,
  table: ({ children }: { children?: ReactNode }) =>
    <div className="study-table-scroll"><table>{children}</table></div>,
  img: ImagePlaceholder,
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
