import { useEffect, useState, type FormEvent } from "react";
import { useLocale, useT } from "../i18n";
import { PageHead } from "../components/PageHead";
import { BankQuestionRow, BankSearch, BankUploadPicker } from "../components/BankPresentation";
import "../styles/bank-presentation.css";
import { messageFor, renderMessage, useWorkspaceApi, type MessageKey, type UiMessage } from "./api";
import { directionExample, directionLabel, useLibraryRole, type LibraryRole, type Selection } from "./libraryRole";

export { RegisteredOnboarding } from "./RegisteredOnboarding";

type TopicLabel = (role: string | null, topic: string) => string;

type Mode = "study" | "interview";
type PracticeQuestion = { id: number; title: string };
type Props = {
  onPractice: (question: PracticeQuestion, mode: Mode) => void;
  onCoach: () => void;
  openImport?: boolean;
};

type Question = PracticeQuestion & {
  role: string | null;
  topic: string;
  answer: string;
  filename: string | null;
};
type QuestionPage = { questions: Question[]; next_before: number | null };
type ImportSummary = {
  id: number;
  filename: string;
  role: string;
  topic: string;
  status: "FAILED" | "REVIEW" | "APPROVED";
  error: string | null;
  suggested: boolean;
};
type CandidateEdit = { id: number; title: string; answer: string; selected: boolean };
type Candidate = CandidateEdit & {
  status: "DRAFT" | "APPROVED" | "MERGED";
  question_id: number | null;
  source_title: string | null;
  source_answer: string;
  ai_title: boolean;
  ai_answer: string | null;
};
type ImportDraft = ImportSummary & { source_text: string; items: Candidate[] };
type ImportPage = { imports: ImportSummary[]; next_before: number | null };
type Privacy = { region: string; provider: string };

const importStatusKeys = { FAILED: 'registered.importFailed', REVIEW: 'registered.importPending', APPROVED: 'registered.importReviewed' };

function errorMessage(error: unknown) {
  return messageFor(error, 'registered.actionFailed');
}

function QuestionCard({
  question,
  topicLabel,
  onPractice,
}: {
  question: Question;
  topicLabel: string;
  onPractice: Props["onPractice"];
}) {
  const t = useT();
  const { send } = useWorkspaceApi();
  const [feedback, setFeedback] = useState("");
  const [status, setStatus] = useState<MessageKey | null>(null);
  const [error, setError] = useState<UiMessage | null>(null);
  const [sending, setSending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!feedback.trim()) return;
    setSending(true);
    setError(null);
    setStatus(null);
    try {
      await send("/feedback", "POST", { question_id: question.id, text: feedback.trim() });
      setFeedback("");
      setStatus('registered.feedbackSent');
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setSending(false);
    }
  }

  return (
    <BankQuestionRow
      title={question.title}
      metadata={<>
        <span>{topicLabel}</span>
        <span>{t('registered.privateQuestion')}</span>
        {question.filename && <small>{t('registered.importedQuestionSource', { filename: question.filename })}</small>}
      </>}
      actions={<>
        <button type="button" className="btn btn-primary" onClick={() => onPractice(question, "study")}>
          {t('registered.study')}
        </button>
        <button type="button" className="btn btn-outline" onClick={() => onPractice(question, "interview")}>
          {t('registered.interview')}
        </button>
      </>}
    >
      {question.answer && (
        <details>
          <summary>{t('registered.viewReferenceAnswer')}</summary>
          <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{question.answer}</p>
        </details>
      )}
      <details>
        <summary>{t('registered.reportQuestion')}</summary>
        <form className="stack" onSubmit={submit}>
          <label>
            {t('registered.questionFeedback')}
            <textarea
              value={feedback}
              onChange={(event) => setFeedback(event.target.value)}
              maxLength={1000}
              required
            />
          </label>
          <small className="muted">{t('registered.questionFeedbackHelp')}</small>
          <button type="submit" className="btn btn-outline" disabled={sending}>
            {sending ? t('registered.sendingFeedback') : t('registered.sendFeedback')}
          </button>
          {error && (
            <p role="alert" className="error">
              {renderMessage(error, t)}
            </p>
          )}
          {status && <p role="status">{t(status)}</p>}
        </form>
      </details>
    </BankQuestionRow>
  );
}

function QuestionList({
  role,
  topic,
  query,
  topicLabel,
  onPractice,
}: {
  role: string;
  topic: string | null;
  query: string;
  topicLabel: TopicLabel;
  onPractice: Props["onPractice"];
}) {
  const t = useT();
  const { request } = useWorkspaceApi();
  const [page, setPage] = useState<QuestionPage>();
  const [error, setError] = useState<UiMessage | null>(null);
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);

  function path(before?: number) {
    const params = new URLSearchParams({ role });
    if (topic) params.set("topic", topic);
    if (query) params.set("q", query);
    if (before) params.set("before", String(before));
    return "/library/questions?" + params;
  }

  useEffect(() => {
    const lifetime = new AbortController();
    request<QuestionPage>(path(), { signal: lifetime.signal }).then(
      (result) => {
        setPage(result);
        setError(null);
      },
      (failure: unknown) => {
        if (!lifetime.signal.aborted) setError(errorMessage(failure));
      },
    );
    return () => lifetime.abort();
  }, [request, role, topic, query, retry]);

  async function more(before: number) {
    setLoading(true);
    setError(null);
    try {
      const result = await request<QuestionPage>(path(before));
      setPage((previous) => ({
        questions: [...(previous?.questions ?? []), ...result.questions],
        next_before: result.next_before,
      }));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {error && (
        <div role="alert" className="error">
          {renderMessage(error, t)}{" "}
          <button
            type="button"
            className="btn btn-outline"
            onClick={() => setRetry((count) => count + 1)}
          >
            {t('registered.retry')}
          </button>
        </div>
      )}
      {page ? (
        page.questions.length === 0 ? (
          <p className="muted">{query ? t('registered.noMatchingQuestions') : t('registered.emptyQuestionBank')}</p>
        ) : (
          <div className="stack">
            {page.questions.map((question) => (
              <QuestionCard key={question.id} question={question} topicLabel={topicLabel(question.role ?? role, question.topic)} onPractice={onPractice} />
            ))}
            {page.next_before !== null && (
              <button
                type="button"
                className="btn btn-outline"
                disabled={loading}
                onClick={() => {
                  if (page.next_before !== null) void more(page.next_before);
                }}
              >
                {t('registered.loadMoreQuestions')}
              </button>
            )}
          </div>
        )
      ) : (
        !error && <p role="status">{t('registered.loading')}</p>
      )}
    </>
  );
}

function Imports({ selection, topicLabel, onSaved }: { selection: Selection | null; topicLabel: TopicLabel; onSaved: () => void }) {
  const t = useT();
  const { request, send } = useWorkspaceApi();
  const [history, setHistory] = useState<ImportPage>();
  const [draft, setDraft] = useState<ImportDraft>();
  const [privacy, setPrivacy] = useState<Privacy>();
  const [privacyError, setPrivacyError] = useState(false);
  const [privacyRetry, setPrivacyRetry] = useState(0);
  const [historyRetry, setHistoryRetry] = useState(0);
  const [file, setFile] = useState<File>();
  const [busy, setBusy] = useState<MessageKey | null>(null);
  const [error, setError] = useState<UiMessage | null>(null);
  const [status, setStatus] = useState<MessageKey | null>(null);

  useEffect(() => {
    let active = true;
    request<Privacy>("/privacy").then(
      (result) => {
        if (active) {
          setPrivacy(result);
          setPrivacyError(false);
        }
      },
      () => {
        if (active) setPrivacyError(true);
      },
    );
    return () => {
      active = false;
    };
  }, [request, privacyRetry]);

  useEffect(() => {
    let active = true;
    request<ImportPage>("/library/imports").then(
      (result) => {
        if (active) setHistory(result);
      },
      (failure: unknown) => {
        if (active) setError(errorMessage(failure));
      },
    );
    return () => {
      active = false;
    };
  }, [request, historyRetry]);

  async function operate(label: MessageKey, action: () => Promise<void>) {
    setBusy(label);
    setError(null);
    setStatus(null);
    try {
      await action();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(null);
    }
  }

  function show(result: ImportDraft) {
    setDraft(result);
    setHistory((previous) => {
      const entries = previous?.imports ?? [];
      return {
        imports: entries.some((entry) => entry.id === result.id)
          ? entries.map((entry) => (entry.id === result.id ? result : entry))
          : [result, ...entries],
        next_before: previous?.next_before ?? null,
      };
    });
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || !selection || !privacy) return;
    const body = new FormData();
    body.set("file", file);
    body.set("role", selection.role);
    body.set("topic", selection.topic);
    void operate('registered.importParsing', async () => {
      const result = await request<ImportDraft>("/library/imports", { method: "POST", body });
      show(result);
      if (result.status === "FAILED") return;
      setFile(undefined);
      setStatus('registered.importParsed');
    });
  }

  function update(id: number, change: Partial<CandidateEdit>) {
    setDraft(
      (previous) =>
        previous && {
          ...previous,
          items: previous.items.map((item) => (item.id === id ? { ...item, ...change } : item)),
        },
    );
  }

  function save(approve: boolean) {
    if (!draft) return;
    const items = draft.items
      .filter((item) => item.status === "DRAFT")
      .map(({ id, title, answer, selected }) => ({
        id,
        title,
        answer,
        selected,
      }));
    void operate(approve ? 'registered.importSavingQuestions' : 'registered.importSavingDraft', async () => {
      const result = await send<ImportDraft>(
        "/library/imports/" + draft.id + (approve ? "/approve" : "/draft"),
        approve ? "POST" : "PUT",
        { items },
      );
      show(result);
      if (approve) onSaved();
      setStatus(approve ? 'registered.importSavedQuestions' : 'registered.importSavedDraft');
    });
  }

  function suggest(batch: ImportDraft) {
    const items = batch.items
      .filter((item) => item.status === "DRAFT")
      .map(({ id, title, answer, selected }) => ({
        id,
        title,
        answer,
        selected,
      }));
    void operate('registered.importExtractingQuestions', async () => {
      if (items.length) {
        await send("/library/imports/" + batch.id + "/draft", "PUT", { items });
      }
      show(await send<ImportDraft>("/library/imports/" + batch.id + "/suggest", "POST", {}));
      setStatus('registered.importQuestionsExtracted');
    });
  }

  return (
    <section className="panel stack" aria-labelledby="library-import-heading">
      <div>
        <p className="eyebrow">{t('registered.privateQuestionBank')}</p>
        <h2 id="library-import-heading">{t('registered.importInterviewNotes')}</h2>
        <p className="muted">{t('registered.supportedImportFiles')}</p>
      </div>
      {privacy ? (
        <p className="muted">
          {t('registered.importPrivacyDetails', { region: privacy.region, provider: privacy.provider })}
        </p>
      ) : privacyError ? (
        <div className="error" role="alert">
          {t('registered.importPrivacyUnavailable')}
          <button
            type="button"
            className="btn btn-outline"
            onClick={() => setPrivacyRetry((count) => count + 1)}
          >
            {t('registered.retry')}
          </button>
        </div>
      ) : (
        <p role="status">{t('registered.importPrivacyLoading')}</p>
      )}
      {selection ? (
        <form className="stack" onSubmit={upload}>
          <p>
            {t('registered.importDestination', { role: selection.role, topic: topicLabel(selection.role, selection.topic) })}
          </p>
          <BankUploadPicker
            accept=".pdf,.docx,.md,.markdown"
            description={t('registered.importFileRequirements')}
            disabled={!privacy || !!busy}
            onFiles={(files) => {
              if (files && files.length > 1) {
                setError({ key: 'registered.importOneFileOnly' });
                return;
              }
              setFile(files?.[0]);
              setError(null);
            }}
          />
          {file && <p className="muted">{file.name}</p>}
          <div className="actions">
            <button type="submit" className="btn btn-primary" disabled={!privacy || !file || !!busy}>
              {t('registered.uploadAndPreview')}
            </button>
          </div>
        </form>
      ) : (
        <p className="muted">{t('registered.selectTopicToImport')}</p>
      )}
      {busy && <p role="status">{t(busy)}</p>}
      {error && (
        <div className="error" role="alert">
          {renderMessage(error, t)}
          {!history && (
            <button
              type="button"
              className="btn btn-outline"
              onClick={() => setHistoryRetry((count) => count + 1)}
            >
              {t('registered.retryImportLoad')}
            </button>
          )}
        </div>
      )}
      {status && <p role="status">{t(status)}</p>}

      {draft && (
        <div className="card stack">
          <div className="row">
            <h3>{draft.filename}</h3>
            <button
              type="button"
              className="btn btn-outline"
              disabled={!!busy}
              onClick={() => setDraft(undefined)}
            >
              {t('registered.closeImportPreview')}
            </button>
          </div>
          <p className="muted">
            {t('registered.importRecordContext', { role: draft.role, topic: topicLabel(draft.role, draft.topic), status: t(importStatusKeys[draft.status]) })}
          </p>
          {draft.error && (
            <p role="alert" className="error">
              {draft.error}
            </p>
          )}
          {!!draft.source_text && (
            <details>
              <summary>{t('registered.viewExtractedText')}</summary>
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                  maxHeight: "18rem",
                  overflowY: "auto",
                }}
              >
                {draft.source_text}
              </pre>
            </details>
          )}
          {draft.status === "REVIEW" && !draft.suggested && (
            <div className="actions">
              <button
                type="button"
                className="btn btn-outline"
                disabled={!privacy || !!busy}
                onClick={() => suggest(draft)}
              >
                {t('registered.extractQuestionsWithAi')}
              </button>
              {!draft.items.length && (
                <small className="muted">
                  {t('registered.noStructuredQuestions')}
                </small>
              )}
            </div>
          )}
          {!!draft.items.length && (
            <form
              className="stack"
              onSubmit={(event) => {
                event.preventDefault();
                save(true);
              }}
            >
              {draft.items.map((item, index) => (
                <fieldset
                  key={item.id}
                  className="card stack"
                  disabled={item.status !== "DRAFT" || !!busy}
                >
                  <legend>
                    {t(item.status === "APPROVED" ? 'registered.importCandidateApproved'
                      : item.status === "MERGED" ? 'registered.importCandidateMerged'
                      : 'registered.importCandidateNumber', { number: index + 1 })}
                  </legend>
                  <label className="row">
                    <input
                      type="checkbox"
                      checked={item.selected}
                      onChange={(event) => update(item.id, { selected: event.target.checked })}
                    />
                    {t('registered.saveThisQuestion')}
                  </label>
                  <label>
                    {t('registered.question')}
                    <input
                      value={item.title}
                      required
                      minLength={2}
                      maxLength={240}
                      onChange={(event) => update(item.id, { title: event.target.value })}
                    />
                  </label>
                  <label>
                    {t('registered.referenceAnswer')}
                    <textarea
                      value={item.answer}
                      maxLength={4000}
                      rows={4}
                      onChange={(event) => update(item.id, { answer: event.target.value })}
                    />
                  </label>
                  {item.ai_title && (
                    <small className="muted">{t('registered.verifyAiDraftQuestion')}</small>
                  )}
                  {item.ai_answer && (
                    <details>
                      <summary>{t('registered.aiAnswerApproach')}</summary>
                      <p>{item.ai_answer}</p>
                    </details>
                  )}
                  {(item.source_title || item.source_answer) && (
                    <details>
                      <summary>{t('registered.originalQuestionAndAnswer')}</summary>
                      {item.source_title && <p>{item.source_title}</p>}
                      <p style={{ whiteSpace: "pre-wrap" }}>
                        {item.source_answer || t('registered.noOriginalAnswer')}
                      </p>
                    </details>
                  )}
                </fieldset>
              ))}
              {draft.status === "REVIEW" && (
                <div className="actions">
                  <button
                    type="button"
                    className="btn btn-outline"
                    disabled={!!busy}
                    onClick={() => save(false)}
                  >
                    {t('registered.saveDraft')}
                  </button>
                  <button
                    type="submit"
                    className="btn btn-primary"
                    disabled={
                      !!busy ||
                      !draft.items.some((item) => item.status === "DRAFT" && item.selected)
                    }
                  >
                    {t('registered.saveSelectedQuestions')}
                  </button>
                </div>
              )}
            </form>
          )}
        </div>
      )}
      {!!history?.imports.length && (
        <div className="stack">
          <h3>{t('registered.importHistory')}</h3>
          {history.imports.map((entry) => (
            <div className="card row" key={entry.id}>
              <div>
                <strong>{entry.filename}</strong>
                <p className="muted">
                  {t('registered.importRecordContext', { role: entry.role, topic: topicLabel(entry.role, entry.topic), status: t(importStatusKeys[entry.status]) })}
                </p>
              </div>
              <button
                type="button"
                className="btn btn-outline"
                disabled={!!busy}
                onClick={() =>
                  void operate('registered.importOpening', async () =>
                    show(await request<ImportDraft>("/library/imports/" + entry.id)),
                  )
                }
              >
                {t('registered.openImportRecord')}
              </button>
            </div>
          ))}
          {history.next_before !== null && (
            <button
              type="button"
              className="btn btn-outline"
              disabled={!!busy}
              onClick={() =>
                void operate('registered.loading', async () => {
                  const result = await request<ImportPage>(
                    "/library/imports?before=" + history.next_before,
                  );
                  setHistory({
                    imports: [...history.imports, ...result.imports],
                    next_before: result.next_before,
                  });
                })
              }
            >
              {t('registered.loadMoreImports')}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

export default function Library({ onPractice, onCoach, openImport = false }: Props) {
  const t = useT();
  const locale = useLocale();
  const { send } = useWorkspaceApi();
  const {
    roles, roleInput, setRoleInput, jd, setJd, busy, error, status, setStatus, setRetry,
    active, direction, selection, applyRole, operate, saveRole, selectDirection,
  } = useLibraryRole();
  const [showAll, setShowAll] = useState(false);
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (openImport && roles) document.getElementById("registered-import")?.scrollIntoView();
  }, [openImport, roles?.selected_role]);

  function topicLabel(role: string | null, topic: string) {
    const saved = roles?.roles.find(item => item.role === role);
    const matching = saved?.directions.find(item => item.name === topic);
    return matching ? directionLabel(matching, t) : topic;
  }

  return (
    <div className="registered-bank stack">
      <PageHead title={t('registered.interviewQuestionBank')} sub={active ? (direction ? t('registered.questionBankScope', { role: active.role, topic: directionLabel(direction, t) }) : active.role) : t('registered.questionBankSubtitle')}>
        <button type="button" className="btn btn-outline" onClick={() => document.getElementById("registered-import")?.scrollIntoView()}>{t('registered.importInterviewNotes')}</button>
        <button type="button" className="btn btn-primary" onClick={onCoach}>{t('registered.openStudyCoach')}</button>
      </PageHead>
      <details className="card bank-role-options">
        <summary>{t('registered.editRoleAndTopic')}</summary>
        <div className="stack" role="region" aria-label={t('registered.practiceByRole')}>
        {!roles &&
          (error ? (
            <button
              type="button"
              className="btn btn-outline"
              onClick={() => setRetry((count) => count + 1)}
            >
              {t('registered.reloadRoles')}
            </button>
          ) : (
            <p role="status">{t('registered.loading')}</p>
          ))}

        {roles && (
          <>
            {!!roles.roles.length && (
              <label>
                {t('registered.savedRoles')}
                <select
                  value={roles.selected_role ?? ""}
                  disabled={!!busy}
                  onChange={(event) => {
                    const role = event.target.value;
                    if (!role) return;
                    void operate('registered.switchingRoles', async () => {
                      applyRole(await send<LibraryRole>("/library/role", "PUT", { role }));
                      setShowAll(false);
                    });
                  }}
                >
                  <option value="" disabled>
                    {t('registered.selectRole')}
                  </option>
                  {roles.roles.map((role) => (
                    <option key={role.role}>{role.role}</option>
                  ))}
                </select>
              </label>
            )}
            <form className="stack" onSubmit={(event) => {
              setShowAll(false);
              void saveRole(event);
            }}>
              <label>
                {t('registered.targetRole')}
                <input
                  value={roleInput}
                  onChange={(event) => {
                    setRoleInput(event.target.value);
                    setJd(
                      roles.roles.find((role) => role.role === event.target.value.trim())?.jd ?? "",
                    );
                  }}
                  placeholder={t('registered.rolePlaceholder')}
                  maxLength={120}
                  required
                />
              </label>
              <label>
                {t('registered.optionalJobRequirements')}
                <textarea
                  value={jd}
                  onChange={(event) => setJd(event.target.value)}
                  rows={3}
                  maxLength={6000}
                  placeholder={t('registered.jobRequirementsPlaceholder')}
                />
              </label>
              <div className="actions">
                <button type="submit" className="btn btn-primary" disabled={!!busy || !roleInput.trim()}>
                  {t('registered.saveRole')}
                </button>
              </div>
            </form>
          </>
        )}
        {active && (
          <>
            <div>
              <h3>{t('registered.practiceTopic')}</h3>
              <p className="muted">{t('registered.topicRecommendationsHelp')}</p>
            </div>
            <div className="grid">
              {active.directions.map((item) => (
                <div key={item.name} className="card stack">
                  <div>
                    <strong>{directionLabel(item, t)}</strong>
                    {" · "}
                    <span className="muted">{item.origin === "preset" ? t('registered.presetTopic') : t('registered.aiSuggestedTopic')}</span>
                  </div>
                  <p>{directionExample(item, locale)}</p>
                  <button
                    type="button"
                    className={item.name === active.selected_topic ? "btn btn-primary" : "btn btn-outline"}
                    aria-pressed={item.name === active.selected_topic}
                    disabled={!!busy}
                    onClick={() => void selectDirection(item.name, () => setShowAll(false))}
                  >
                    {item.name === active.selected_topic ? t('registered.selectedTopic') : t('registered.selectTopic')}
                  </button>
                </div>
              ))}
            </div>
            <div className="actions">
              <button
                type="button"
                className="btn btn-outline"
                disabled={
                  !!busy || roleInput.trim() !== active.role || jd.trim() !== (active.jd ?? "")
                }
                onClick={() =>
                  void operate('registered.topicRecommendationsLoading', async () => {
                    applyRole(
                      await send<LibraryRole>("/library/directions", "POST", { role: active.role }),
                    );
                    setStatus('registered.topicRecommendationsUpdated');
                  })
                }
              >
                {t('registered.suggestTopics')}
              </button>
              {(roleInput.trim() !== active.role || jd.trim() !== (active.jd ?? "")) && (
                <small className="muted">{t('registered.saveBeforeSuggestions')}</small>
              )}
            </div>
          </>
        )}
        </div>
      </details>
        {busy && <p role="status">{t(busy)}</p>}
        {error && (
          <p role="alert" className="error">
            {renderMessage(error, t)}
          </p>
        )}
        {status && <p role="status">{t(status)}</p>}
        {selection && (
          <div className="actions">
            <button
              type="button"
              className="btn btn-primary"
              disabled={!!busy}
              onClick={() =>
                void operate('registered.sampleOpening', async () => {
                  const question = await send<Question>("/library/example", "POST", selection);
                  setRevision((count) => count + 1);
                  onPractice(question, "study");
                })
              }
            >
              {t('registered.trySampleQuestion')}
            </button>
            <button
              type="button"
              className="btn btn-outline"
              disabled={!!busy}
              onClick={() =>
                void operate('registered.starterQuestionsGenerating', async () => {
                  await send<{ questions: Question[] }>("/library/starter", "POST", selection);
                  setRevision((count) => count + 1);
                  setStatus('registered.starterQuestionsSaved');
                })
              }
            >
              {t('registered.generateStarterQuestions')}
            </button>
          </div>
        )}
      {active && (
        <section className="panel stack" aria-labelledby="library-questions-heading">
          <div className="row">
            <div>
              <h2 id="library-questions-heading">{t('registered.myQuestionBank')}</h2>
              <p className="muted">
                {t('registered.questionBankScope', { role: active.role, topic: showAll || !direction ? t('registered.allTopics') : directionLabel(direction, t) })}
              </p>
            </div>
            {direction && (
              <button
                type="button"
                className="btn btn-outline"
                onClick={() => setShowAll((value) => !value)}
              >
                {showAll ? t('registered.currentTopicOnly') : t('registered.showAllTopics')}
              </button>
            )}
          </div>
          <BankSearch value={query} onChange={setQuery} maxLength={240} />
          <QuestionList
            key={JSON.stringify([active.role, showAll ? null : direction?.name, query.trim(), revision])}
            role={active.role}
            topicLabel={topicLabel}
            topic={showAll ? null : (direction?.name ?? null)}
            query={query.trim()}
            onPractice={onPractice}
          />
        </section>
      )}
      <div id="registered-import"><Imports selection={selection} topicLabel={topicLabel} onSaved={() => setRevision((count) => count + 1)} /></div>
    </div>
  );
}
