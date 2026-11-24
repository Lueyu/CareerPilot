import { useT } from '../i18n';
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

export default function AnswerGuide({guide, beginnerExample}: {guide: TeachingGuide; beginnerExample?: string | null}) {
  const t = useT();

  return <section className="teaching-guide" aria-label={t("study.answerFromScratch")}>
    <h3>{t("study.answerFromScratch")}</h3>
    <p>{guide.plain}</p>
    <p className="guide-analogy"><strong>{t("study.analogy")}</strong> {beginnerExample || guide.analogy}</p>
    <figure>
      <figcaption>{t("study.walkthrough")}</figcaption>
      <ol className="guide-flow">{guide.flow.map((node, i) => <li key={i}>{node}</li>)}</ol>
    </figure>
    <h4>{t("study.stepByStep")}</h4>
    <ol>{guide.steps.map((step, i) => <li key={i}>{step}</li>)}</ol>
    <h4>{t("study.workedExample")}</h4>
    <p>{guide.example}</p>
    <div className="guide-table-scroll" role="region" aria-label={t("study.comparisonTable")} tabIndex={0}>
      <table><thead><tr>{guide.table.headers.map((cell, i) => <th key={i} scope="col">{cell}</th>)}</tr></thead>
        <tbody>{guide.table.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>)}</tbody>
      </table>
    </div>
    <h4>{t("study.applyInProject", { name: guide.project.name })}</h4>
    <p>{guide.project.application}</p>
    <p className="guide-source">{t("study.projectMaterialPrefix")}<code>{guide.project.anchor}</code></p>
    <h4>{t("study.interviewPhrasing")}</h4>
    <blockquote>{guide.pitch}</blockquote>
    <details><summary>{t("study.tryAnsweringQuestion", { question: guide.check.question })}</summary><p>{guide.check.answer}</p></details>
  </section>;
}
