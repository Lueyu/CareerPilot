import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError, api, workspaceStorageKey } from '../../api/client';
import { chatHref, errorMessage, examHref, positiveId, useCopy, type Attempt, type ExamMode, type ExamSource, type QuestionType } from './examState';
import './exam.css';

type InterviewSet = { id: string; title: string; project_name?: string };
type SetsState = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; items: InterviewSet[] };
const clipboard = new URL('./assets/clipboard.svg', import.meta.url).href;

export function ExamConfiguration({ sessionId, topic = '' }: { sessionId?: number; topic?: string }) {
  const copy = useCopy();
  const navigate = useNavigate();
  const [mode, setMode] = useState<ExamMode>('test');
  const [source, setSource] = useState<ExamSource>('bank');
  const [types, setTypes] = useState<QuestionType[]>(['mcq', 'open']);
  const [difficulty, setDifficulty] = useState(0);
  const [minutes, setMinutes] = useState(30);
  const [count, setCount] = useState(4);
  const [query, setQuery] = useState(topic.slice(0, 120));
  const [setId, setSetId] = useState('');
  const [sets, setSets] = useState<SetsState>({ kind: 'loading' });
  const [setRetry, setSetRetry] = useState(0);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const activeRequest = useRef(false);
  const lastRequest = useRef<{ signature: string; key: string }>();
  const storageKey = workspaceStorageKey('ia-exam-create:' + (positiveId(sessionId) ?? 'new'));

  useEffect(() => {
    if (source !== 'interview_set') return;
    let ignore = false;
    setSets({ kind: 'loading' });
    api.get<{ items: InterviewSet[] }>('/bank/interview-sets').then(
      result => { if (!ignore) setSets({ kind: 'ready', items: result.items }); },
      error => { if (!ignore) setSets({ kind: 'error', message: errorMessage(error) }); },
    );
    return () => { ignore = true; };
  }, [source, setRetry]);

  const selectedSet = sets.kind === 'ready' ? sets.items.find(item => item.id === setId) : undefined;
  const changeMode = (value: ExamMode) => {
    setMode(value);
    setSource(value === 'test' ? 'bank' : 'catalog');
    setError('');
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (activeRequest.current || !types.length || (source === 'interview_set' && !selectedSet)) return;
    activeRequest.current = true;
    setStarting(true);
    setError('');
    const body = {
      kind: 'bank_exam',
      question_count: count,
      duration_minutes: minutes,
      interview_agent: {
        mode, source, question_types: [...types].sort(), difficulty: difficulty || null,
        origin_session_id: positiveId(sessionId) ?? null, topic: query.trim(),
        interview_set_id: selectedSet && source === 'interview_set' ? selectedSet.id : null,
      },
    };
    const signature = JSON.stringify(body);
    let key = lastRequest.current?.signature === signature ? lastRequest.current.key : undefined;
    try {
      // Only retain the random key and a hash of the configuration between reloads.
      const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(signature));
      const fingerprint = Array.from(new Uint8Array(bytes)).join('.');
      try {
        const [storedFingerprint, storedKey] = (sessionStorage.getItem(storageKey) ?? '').split(':');
        if (storedFingerprint === fingerprint && storedKey) key = storedKey;
      } catch { /* In-memory request identity still covers retries on this page. */ }
      key ??= crypto.randomUUID();
      lastRequest.current = { signature, key };
      try { sessionStorage.setItem(storageKey, fingerprint + ':' + key); } catch { /* Optional storage. */ }
      const attempt = await api.post<Attempt>('/exams', { ...body, idempotency_key: key });
      try { sessionStorage.removeItem(storageKey); } catch { /* Optional storage. */ }
      lastRequest.current = undefined;
      navigate(examHref(attempt.id, attempt.status !== 'ACTIVE'));
    } catch (failure) {
      setError(errorMessage(failure));
      if (failure instanceof ApiError && failure.status >= 400 && failure.status < 500) {
        try { sessionStorage.removeItem(storageKey); } catch { /* Optional storage. */ }
        lastRequest.current = undefined;
      }
    } finally {
      activeRequest.current = false;
      setStarting(false);
    }
  };

  return <form className="ia-exam ia-exam-config ia-exam-card" onSubmit={submit}>
    <div className="ia-exam-card-heading">
      <img className="ia-exam-icon-tile" src={clipboard} alt="" />
      <h2>{query.trim() || copy('配置模拟试卷', 'Configure your paper')}</h2>
      {count > 0 && <span>{copy(`计划 ${count} 题`, `${count} planned`)}</span>}
    </div>
    <p>{mode === 'test'
      ? copy('从可测且未曝光的题目中组卷。评分完成后，符合条件的独立作答可进入能力评估。',
        'Build a paper from unseen, assessment-ready questions. Eligible independent answers can inform competency assessment after scoring.')
      : copy('练习按相同的计时与评分流程进行，成绩只用于回顾。', 'Practice uses the same timer and scoring flow. Its results are for review only.')}</p>
    <div className="ia-exam-config-grid">
      <fieldset disabled={starting}>
        <legend>{copy('作答用途', 'Assessment mode')}</legend>
        <div className="ia-exam-pills">
          {(['test', 'practice'] satisfies ExamMode[]).map(value =>
            <button type="button" key={value} aria-pressed={mode === value} onClick={() => changeMode(value)}>
              {value === 'test' ? copy('正式测试', 'Test') : copy('限时练习', 'Timed practice')}
            </button>)}
        </div>
        <fieldset className="ia-exam-types">
          <legend>{copy('题型', 'Question types')}</legend>
          <div className="ia-exam-pills">
            {(['mcq', 'open', 'code'] satisfies QuestionType[]).map(value =>
              <button type="button" key={value} aria-pressed={types.includes(value)}
                onClick={() => setTypes(current => current.includes(value) ? current.filter(type => type !== value) : [...current, value])}>
                {value === 'mcq' ? copy('单选', 'Single choice')
                  : value === 'open' ? copy('简答', 'Written answer') : copy('代码', 'Code')}
              </button>)}
            <button type="button" disabled>{copy('多选', 'Multiple selection')}</button>
          </div>
          <small>{copy('代码题来自带编程标签的简答题，按题目评分标准评阅。当前题库评分暂不支持多选。',
            'Code questions come from tagged written-response questions and use their rubric. Multiple selection is unavailable.')}</small>
        </fieldset>
        <label>{copy('题目来源', 'Question source')}
          <select value={source} onChange={event => {
            const value = event.target.value;
            if (value === 'bank' || value === 'catalog' || value === 'library' || value === 'interview_set') setSource(value);
          }}>
            <option value="bank">{copy('题库 · 可测题目', 'Bank · Assessment-ready')}</option>
            {mode === 'practice' && <>
              <option value="catalog">{copy('默认面试题库', 'Default interview bank')}</option>
              <option value="library">{copy('我的已入库题目', 'My accepted questions')}</option>
              <option value="interview_set">{copy('面经套题', 'Interview question set')}</option>
            </>}
          </select>
        </label>
        {source === 'interview_set' && <>
          {sets.kind === 'loading' && <small role="status">{copy('正在读取面经套题…', 'Loading interview sets…')}</small>}
          {sets.kind === 'error' && <p role="alert">{sets.message} <button type="button" onClick={() => setSetRetry(value => value + 1)}>{copy('重试', 'Retry')}</button></p>}
          {sets.kind === 'ready' && (sets.items.length ? <label>{copy('面经套题', 'Interview question set')}
            <select value={setId} onChange={event => setSetId(event.target.value)}>
              <option value="">{copy('请选择套题', 'Select a set')}</option>
              {sets.items.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
          </label> : <small>{copy('暂时没有已保存的面经套题。', 'There are no saved interview sets yet.')}</small>)}
        </>}
        <label>{copy('主题（可选）', 'Topic (optional)')}
          <input type="search" maxLength={120} value={query} onChange={event => setQuery(event.target.value)} />
        </label>
      </fieldset>
      <fieldset disabled={starting}>
        <label className="ia-exam-slider-label">
          <span>{copy('难度', 'Difficulty')}<output>{difficulty ? `${difficulty} / 5` : copy('不限', 'Any')}</output></span>
          <input type="range" min="0" max="5" value={difficulty} onChange={event => setDifficulty(event.target.valueAsNumber)} />
        </label>
        <label>{copy('考试时长（分钟）', 'Time limit (minutes)')}
          <input type="number" min={1} max={240} step={1} required value={minutes || ''} onChange={event => setMinutes(event.target.valueAsNumber)} />
        </label>
        <label>{copy('题目数量', 'Number of questions')}
          <input type="number" min={1} max={20} step={1} required value={count || ''} onChange={event => setCount(event.target.valueAsNumber)} />
        </label>
        <p className="ia-exam-reference-source"><strong>{copy('标准答案参考项目', 'Project reference for answers')}</strong><br />
          {selectedSet?.project_name || copy('随题目使用已保存的来源。题目缺少项目来源时，使用题库参考答案。',
            'Saved sources are inherited from the questions. Where no project source is attached, the report uses the bank’s reference answer.')}</p>
      </fieldset>
    </div>
    {error && <p className="ia-exam-error" role="alert">{error}</p>}
    <div className="ia-exam-actions">
      <button className="ia-exam-button ia-exam-primary" disabled={starting || !types.length || (source === 'interview_set' && !selectedSet)}>
        {starting ? copy('正在组卷…', 'Building paper…') : copy('开始作答', 'Start answering')}
      </button>
      <Link className="ia-exam-button" to={chatHref(sessionId)}>{copy('返回当前对话', 'Return to chat')}</Link>
    </div>
  </form>;
}
