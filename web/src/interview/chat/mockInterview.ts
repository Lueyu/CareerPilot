import type { Message, WorkflowRun } from "./model";

type MockPhase = "prepare" | "question" | "answer" | "feedback" | "report" | "ready" | "failed";

export function mockInterviewProgress(run: WorkflowRun, messages: Message[]) {
  const turns = messages.filter((message) => {
    const scope = message.workflow;
    return (
      scope &&
      message.id > (run.boundary_message_id ?? 0) &&
      scope.run_id === run.run_id &&
      scope.step === run.current &&
      scope.step_revision === run.step_revision
    );
  });
  const question = turns.find(
    (message) => message.role === "assistant" && message.workflow?.purpose === "mock_question",
  );
  const answer = turns.find(
    (message) => message.role === "user" && message.workflow?.purpose === "mock_answer",
  );
  const feedback = turns.find(
    (message) => message.role === "assistant" && answer && message.request_id === answer.request_id,
  );
  const report = turns.find(
    (message) => message.role === "assistant" && message.workflow?.purpose === "mock_report",
  );
  const reply = report ?? feedback ?? question;
  const phase: MockPhase =
    reply?.status === "failed" || reply?.status === "interrupted"
      ? "failed"
      : report
        ? report.status === "completed" && report.content.trim()
          ? "ready"
          : "report"
        : feedback
          ? feedback.status === "completed" && feedback.content.trim()
            ? "ready"
            : "feedback"
          : question
            ? question.status === "completed" && question.content.trim()
              ? "answer"
              : "question"
            : "prepare";
  return { phase, question, answer, feedback, report, reply };
}
