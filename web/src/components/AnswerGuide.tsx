import React from 'react';
import './answerGuide.css';

export interface TeachingGuide {
  plain: string;
  analogy: string;
  steps: string[];
  flow: string[];
  table: {headers: string[]; rows: string[][]};
  example: string;
  project: {name: string; anchor: string; application: string};
  pitch: string;
  check: {question: string; answer: string};
}

export default function AnswerGuide({guide}: {guide: TeachingGuide}) {
  return <section className="teaching-guide" aria-label="从零理解这道题">
    <h3>从零理解这道题</h3>
    <p>{guide.plain}</p>
    <p className="guide-analogy"><strong>打个比方</strong> {guide.analogy}</p>
    <figure>
      <figcaption>顺着流程看一遍</figcaption>
      <ol className="guide-flow">{guide.flow.map((node, i) => <li key={i}>{node}</li>)}</ol>
    </figure>
    <h4>一步步拆解</h4>
    <ol>{guide.steps.map((step, i) => <li key={i}>{step}</li>)}</ol>
    <h4>走一个具体例子</h4>
    <p>{guide.example}</p>
    <div className="guide-table-scroll" role="region" aria-label="对照表" tabIndex={0}>
      <table><thead><tr>{guide.table.headers.map((cell, i) => <th key={i} scope="col">{cell}</th>)}</tr></thead>
        <tbody>{guide.table.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>)}</tbody>
      </table>
    </div>
    <h4>放到 {guide.project.name} 里想一想</h4>
    <p>{guide.project.application}</p>
    <p className="guide-source">项目材料 <code>{guide.project.anchor}</code></p>
    <h4>面试时可以这样说</h4>
    <blockquote>{guide.pitch}</blockquote>
    <details><summary>自己试答 · {guide.check.question}</summary><p>{guide.check.answer}</p></details>
  </section>;
}
