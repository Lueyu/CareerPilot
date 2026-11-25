import { useLocale, useT } from '../i18n';
import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { DemoBanner, PageHead } from '../components/ui';
import { api, useApi, type ExamHistoryPage } from '../api/client';
import { EXPERIENCED_BANK_PATH, experiencedHistoryPath } from '../lib/experiencedBankPath';
import { track } from '../telemetry';


const STATUS_CLS: Record<string, string> = {
  COMPLETED: 'chip-green',
  ACTIVE: 'chip-amber',
  SCORING: 'chip-purple',
  SUBMITTED: 'chip-slate',
  TRANSCRIPT_CONFIRMED: 'chip-slate',
  FROZEN: 'chip-slate',
  CREATED: 'chip-slate',
  ABORTED: 'chip-slate',
  EXPIRED: 'chip-slate',
  SCORING_FAILED: 'chip-red',
  SCORING_RETRY_WAIT:'chip-amber',NEEDS_REVIEW:'chip-amber',
};
const STATUS_NAME: Record<string, string> = {
  COMPLETED: "study.completed", ACTIVE: "study.inProgress", SCORING: "study.scoring",
  SUBMITTED: "study.submitted", TRANSCRIPT_CONFIRMED: "study.transcriptionConfirmation", FROZEN: "study.locked", CREATED: "study.created",
  ABORTED: "study.abandoned", EXPIRED: "study.expired", SCORING_FAILED: "study.scoringFailed",
  SCORING_RETRY_WAIT:"study.waitingForBackgroundRetry",NEEDS_REVIEW:"study.scoreNeedsReview",
};
const KIND_NAME: Record<string, string> = {
  diagnosis: "study.independentDiagnostic", focused: "study.focusedExam", bank_exam: "study.interviewSetExam", project: "study.projectInterview",
};
const KIND_ICON: Record<string, string> = {
  diagnosis: '🎯', focused: '🧪', bank_exam: '📚', project: '💼',
};
const KIND_DESC: Record<string, string> = {
  diagnosis: "study.diagnosticDescription",
  focused: "study.focusedExamDescription",
  bank_exam: "study.interviewSetDescription",
  project: "study.projectInterviewDescription",
};

const HISTORY_PATH = '/exams/history';

export default function ExamCenter() {
  const t = useT();
  const locale = useLocale();

  const navigate = useNavigate();
  const { data, loading, error } = useApi<ExamHistoryPage>(HISTORY_PATH);
  const [older, setOlder] = useState<Pick<ExamHistoryPage, 'history' | 'next_cursor'> | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<string | null>(null);
  const nextCursor = older ? older.next_cursor : data?.next_cursor;

  async function loadOlder() {
    if (nextCursor == null || loadingOlder) return;
    setLoadingOlder(true);
    setOlderError(null);
    try {
      const page = await api.get<ExamHistoryPage>(`${HISTORY_PATH}?before_id=${nextCursor}`);
      setOlder(previous => {
        if (previous && previous.next_cursor !== nextCursor) return previous;
        return {
          history: [...(previous?.history ?? []), ...page.history],
          next_cursor: page.next_cursor,
        };
      });
    } catch (cause) {
      setOlderError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingOlder(false);
    }
  }

  const kinds = data?.kinds
    ? data.kinds.filter(k => k.kind === 'diagnosis').map((k) => ({
        id: k.kind, icon: KIND_ICON[k.kind] || '📝', title: locale === "en" && KIND_NAME[k.kind] ? t(KIND_NAME[k.kind]) : k.title,
        desc: (locale === "zh-CN" ? k.desc : "") || (KIND_DESC[k.kind] ? t(KIND_DESC[k.kind]) : k.desc) || "",
        meta: [t("study.durationMinutes", { minutes: k.default_minutes }), t("study.attemptCount", { count: k.history_count })],
        primary: k.kind === 'diagnosis',
      }))
    : [];
  const history = [...(data?.history ?? []), ...(older?.history ?? [])].map(h => ({
    id: h.id,
    name: h.experienced_bank_practice || h.title === '社招专项' ? t("study.questionBankPractice") : h.title,
    type: h.experienced_bank_practice ? t("study.questionBankPractice") : (KIND_NAME[h.kind] ? t(KIND_NAME[h.kind]) : h.kind),
    experienced: h.experienced_bank_practice,
    status: h.status, score: h.score, time: h.completed_at || h.started_at,
    report: ['COMPLETED', 'SCORING_FAILED', 'SCORING', 'SCORING_RETRY_WAIT','NEEDS_REVIEW','SUBMITTED','FROZEN','TRANSCRIPT_CONFIRMED'].includes(h.status),
  }));

  return (
    <div>
      <PageHead
        title={t("study.examCenter")}
        sub={t("study.examCenterSubtitle")}
      >
        <Link className="btn btn-outline" to={EXPERIENCED_BANK_PATH}>{t("study.viewQuestionBank")}</Link>
        <button className="btn btn-primary" onClick={() => navigate('/exam/diagnosis')}>{t("study.startIndependentDiagnostic")}</button>
      </PageHead>
      <DemoBanner />

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        {kinds.map(e => (
          <div className="exam-card" key={e.id}>
            <div className={`exam-ic ${e.primary ? '' : 'alt'}`}>{e.icon}</div>
            <div className="exam-title">{e.title}</div>
            <div className="exam-desc">{e.desc}</div>
            <div className="exam-meta">
              {e.meta.map((m: string) => (
                <span key={m}>{m}</span>
              ))}
            </div>
            <button
              className={`btn ${e.primary ? 'btn-primary' : 'btn-outline'}`}
              onClick={() => (track('exam.start', undefined, { kind: e.id }), navigate('/exam/diagnosis'))}
            >
              {e.primary ? t("study.startDiagnostic") : t("study.startExam")}
            </button>
          </div>
        ))}
      </div>

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>{t("study.loadingLiveExamData")}</div>}
      {error && <div className="demo-banner demo-banner-err" style={{ marginBottom: 12 }}>{t("study.examHistoryBackendUnavailable", { reason: error })}</div>}

      <div className="card">
        <div className="card-head">
          <span className="card-title">{t("study.history")}</span>
          {data && <span className="hint" role="status">{t("study.historyRecordCount", { shown: history.length, total: data.total_count })}</span>}
        </div>
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>{t("study.examName")}</th>
                <th>{t("study.type")}</th>
                <th>{t("study.status")}</th>
                <th>{t("study.score")}</th>
                <th>{t("study.time")}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>{!loading && !error && history.length === 0 && <tr><td colSpan={6}>{t("study.noExamHistory")}</td></tr>}
              {history.map(h => (
                <tr key={h.id}>
                  <td className="strong">{h.name}</td>
                  <td><span className="chip chip-slate">{h.type}</span></td>
                  <td><span className={`chip ${STATUS_CLS[h.status] || 'chip-slate'}`}>{STATUS_NAME[h.status] ? t(STATUS_NAME[h.status]) : h.status}</span></td>
                  <td className="num">{h.status!=='COMPLETED' || h.score === null || h.score === undefined ? '—' : <b style={{ color: (h.score ?? 0) >= 70 ? 'var(--green)' : 'var(--amber)' }}>{Math.round(h.score)}</b>}</td>
                  <td className="num">{h.time ? new Date(h.time).toLocaleDateString(locale) : "—"}</td>
                  <td className="right">
                    {h.report ? (
                      <button className="btn btn-ghost btn-sm" onClick={() => navigate(h.experienced ? experiencedHistoryPath(h.id, true) : `/exam/diagnosis/report?id=${h.id}`)}>{h.status==='COMPLETED'?t("study.viewReport"):t("study.viewAttemptStatus")}</button>
                    ) : (
                      h.status === 'ACTIVE' ? <button className="btn btn-ghost btn-sm" onClick={() => navigate(h.experienced ? experiencedHistoryPath(h.id, false) : `/exam/diagnosis?id=${h.id}`)}>{h.experienced ? t("study.resumePractice") : t("study.resumeExam")}</button> : <span>—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {olderError && <p className="demo-banner demo-banner-err" role="alert">{t("study.olderHistoryLoadFailed", { reason: olderError })}</p>}
        {nextCursor != null && (
          <button className="btn btn-outline" type="button" disabled={loadingOlder} onClick={loadOlder}
            style={{ marginTop: 12 }}>
            {loadingOlder ? t("study.loading") : t("study.loadOlderHistory")}
          </button>
        )}
      </div>
    </div>
  );
}
