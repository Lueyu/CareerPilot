import { api, ExamDetail, ExamQuestionView, getWorkspace, ScoringTaskAck } from '../api/client';

type Answers = (number | string)[];
type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const creationKey = (workspace: string) => `cp-exam-create:${workspace}:diagnosis`;
export const draftKey = (id: number) => `cp-exam-draft:${getWorkspace()}:${id}`;

export function clearExamCreation(storage: StorageLike = sessionStorage, workspace = getWorkspace()) {
  storage.removeItem(creationKey(workspace));
}

export async function loadOrCreateExam(id: string | null, transport = api, storage: StorageLike = sessionStorage, workspace = getWorkspace()): Promise<ExamDetail> {
  if (id) {
    if (!/^\d+$/.test(id)) throw new Error('考试 ID 无效');
    return transport.get<ExamDetail>(`/exams/${id}`);
  }
  // Establish the request identity before the first await (including StrictMode replays).
  const key = storage.getItem(creationKey(workspace)) || crypto.randomUUID();
  storage.setItem(creationKey(workspace), key);
  const list = await transport.get<{ history: { id: number; kind: string; status: string }[] }>('/exams');
  const active = list.history.find(e => e.kind === 'diagnosis' && e.status === 'ACTIVE');
  if (active) return transport.get<ExamDetail>(`/exams/${active.id}`);
  return transport.post<ExamDetail>('/exams', { kind: 'diagnosis', idempotency_key: key });
}

export function answerPayload(question: ExamQuestionView, value: number | string | undefined) {
  return question.qtype === 'mcq'
    ? { order_no: question.order_no, choice_index: typeof value === 'number' && value >= 0 ? value : null }
    : { order_no: question.order_no, text_answer: String(value ?? '') };
}

export async function saveExamAnswer(exam: ExamDetail, answers: Answers, index: number, transport = api) {
  return transport.post(`/exams/${exam.id}/answers`, {...answerPayload(exam.questions[index], answers[index]),expected_revision:exam.questions[index].answer?.revision??0});
}

export async function submitExam(exam: ExamDetail, answers: Answers, transport = api, revisions?:number[]) {
  return transport.post<ScoringTaskAck>(`/exams/${exam.id}/submit`,{answers:exam.questions.map((q,i)=>({...answerPayload(q,answers[i]),expected_revision:revisions?.[i]??q.answer?.revision??0}))});
}
