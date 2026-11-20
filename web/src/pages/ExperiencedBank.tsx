import React, { useEffect, useState } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { PageHead } from '../components/ui';
import { ORIGINAL_SCENARIO_LABEL } from '../lib/bankSource';
import { messageOf } from '../lib/questionWorkshop';
import { experiencedPracticePath } from '../lib/experiencedBankPath';

export const EXPERIENCE_LABELS = { mid: '2–4 年', senior: '4 年以上' };
type ExperiencedLevel = keyof typeof EXPERIENCE_LABELS;
const LEVELS: ExperiencedLevel[] = ['mid', 'senior'];

export interface ExperiencedQuestion {
  id: number;
  short_code: string;
  title: string;
  level: ExperiencedLevel;
  tags: string[];
}
interface ExperiencedDomain {
  key: string;
  name: string;
  order: number;
  focus: string;
  questions: ExperiencedQuestion[];
}
type CatalogState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; domains: ExperiencedDomain[] };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

function isQuestion(value: unknown): value is ExperiencedQuestion {
  return isRecord(value) && typeof value.id === 'number' && Number.isSafeInteger(value.id) && value.id > 0 &&
    typeof value.short_code === 'string' && typeof value.title === 'string' &&
    (value.level === 'mid' || value.level === 'senior') &&
    Array.isArray(value.tags) && value.tags.every(tag => typeof tag === 'string');
}

function isDomain(value: unknown): value is ExperiencedDomain {
  return isRecord(value) && typeof value.key === 'string' && typeof value.name === 'string' &&
    typeof value.order === 'number' && Number.isFinite(value.order) && typeof value.focus === 'string' &&
    Array.isArray(value.questions) && value.questions.every(isQuestion);
}

function showElectiveLabel(domain: ExperiencedDomain): boolean {
  return domain.key === 'training' && !domain.name.includes('选修');
}

export default function ExperiencedBank({
  practiceBusy,
  practiceError,
  onPractice,
  onReference,
}: {
  practiceBusy: string | null;
  practiceError: {id: string; message: string} | null;
  onPractice: (question: ExperiencedQuestion) => void;
  onReference: (question: ExperiencedQuestion, domainName: string) => void;
}) {
  const [catalog, setCatalog] = useState<CatalogState>({ kind: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const level = params.get('level') === 'senior' ? 'senior' : 'mid';
  const domainKey = params.get('domain');
  const resumeId = location.state?.experiencedPracticeId;
  const resumePath = typeof resumeId === 'number' && Number.isSafeInteger(resumeId) && resumeId > 0
    ? experiencedPracticePath(resumeId, params)
    : null;
  const selectFilter = (key: 'level' | 'domain', value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: true, state: location.state });
  };

  useEffect(() => {
    let current = true;
    api.get<unknown>('/bank/experienced').then(data => {
      if (!isRecord(data) || !Array.isArray(data.domains) || !data.domains.every(isDomain)) {
        throw new Error('题单格式异常');
      }
      if (current) setCatalog({ kind: 'ready', domains: [...data.domains].sort((a, b) => a.order - b.order) });
    }).catch(error => {
      if (current) setCatalog({ kind: 'error', message: messageOf(error) });
    });
    return () => { current = false; };
  }, [attempt]);

  const domains = catalog.kind === 'ready' ? catalog.domains : [];
  const active = domains.find(domain => domain.key === domainKey) ?? domains[0];
  const questions = active?.questions.filter(question => question.level === level) ?? [];

  return (
    <>
      <PageHead title="社招专项" sub="AI Agent 工程师面试题单，按工作年限与能力域选题。">
        {resumePath && <Link className="btn btn-primary" to={resumePath}>继续本次练习</Link>}
        <Link className="btn btn-outline" to="/bank">返回面经库</Link>
      </PageHead>
      <div className="bank-workspace">
        <section className="card bank-section experienced-guide" aria-label="练习指南">
          <div className="bank-actions" role="group" aria-label="工作年限">
            {LEVELS.map(option => (
              <button
                key={option}
                className={`btn ${level === option ? 'btn-primary' : 'btn-outline'}`}
                aria-pressed={level === option}
                onClick={() => selectFilter('level', option)}
              >{EXPERIENCE_LABELS[option]}</button>
            ))}
          </div>
          <p>先读能力域的考察重点，再用「开始练习」独立作答。交卷后在报告里复盘评分和失分项。</p>
          <p className="hint">算法与模型训练可按岗位要求选练。查看参考会记录辅助条件，建议先独立作答。</p>
        </section>

        {catalog.kind === 'loading' && <p role="status" className="card bank-section">正在加载社招专项题单…</p>}
        {catalog.kind === 'error' && (
          <section className="card bank-section">
            <p role="alert">社招专项题单加载失败：{catalog.message}</p>
            <div>
              <button className="btn btn-outline" onClick={() => {
                setCatalog({ kind: 'loading' });
                setAttempt(value => value + 1);
              }}>重试加载题单</button>
            </div>
          </section>
        )}
        {catalog.kind === 'ready' && !active && <p className="card bank-section">社招专项暂未收录题目。</p>}

        {active && (
          <div className="experienced-layout">
            <nav className="card bank-section experienced-domains" aria-label="能力域">
              <h2>能力域 <span className="hint">· {domains.length} 个</span></h2>
              <div className="experienced-domain-list">
                {domains.map(domain => (
                  <button
                    key={domain.key}
                    className="experienced-domain"
                    aria-pressed={domain.key === active.key}
                    onClick={() => selectFilter('domain', domain.key)}
                  >
                    <span>{domain.name}</span>
                    {showElectiveLabel(domain) && <span className="chip chip-slate">选修</span>}
                  </button>
                ))}
              </div>
            </nav>

            <section className="card bank-section experienced-questions" aria-labelledby="experienced-domain-title">
              <div className="bank-actions">
                <h2 id="experienced-domain-title">{active.name}</h2>
                <span className="chip chip-slate">{EXPERIENCE_LABELS[level]} · {questions.length} 题</span>
                {showElectiveLabel(active) && <span className="chip chip-slate">选修</span>}
              </div>
              <p className="hint experienced-prompt">{active.focus}</p>
              {questions.length === 0 ? <p>这个能力域暂未收录该年限的题目。</p> : (
                <ul className="experienced-question-list">
                  {questions.map(question => (
                    <li key={question.id} className="experienced-question">
                      <div>
                        <h3 id={`experienced-question-${question.id}`} className="experienced-prompt">{question.title}</h3>
                        <div className="q-meta">
                          <span className="chip chip-slate">{EXPERIENCE_LABELS[question.level]}</span>
                          <span>{ORIGINAL_SCENARIO_LABEL}</span>
                          <span className="experienced-question-code">{question.short_code}</span>
                        </div>
                      </div>
                      <div className="bank-actions">
                        <button
                          className="btn btn-primary btn-sm"
                          disabled={practiceBusy !== null}
                          aria-describedby={`experienced-question-${question.id}`}
                          onClick={() => onPractice(question)}
                        >{practiceBusy === question.short_code ? '准备练习…' : practiceError?.id === question.short_code ? '重试练习' : '开始练习'}</button>
                        <button className="btn btn-outline btn-sm" aria-describedby={`experienced-question-${question.id}`} onClick={() => onReference(question, active.name)}>
                          查看参考
                        </button>
                      </div>
                      {practiceError?.id === question.short_code && <p role="alert">{practiceError.message}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
      </div>
    </>
  );
}
