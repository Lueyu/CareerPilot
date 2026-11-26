import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { setLocale } from "../src/i18n.ts";
import MockInterviewGuide from "../src/interview/chat/MockInterviewGuide.tsx";
import { mockInterviewProgress } from "../src/interview/chat/mockInterview.ts";
import { workflowCommand } from "../src/interview/chat/model.ts";

const run = { id: "mock", run_id: "this-run", revision: 3, step_revision: 1, current: 0,
  status: "running", steps: ["准备", "自我介绍", "技术", "系统设计", "行为", "复盘"],
  skipped: [], completed_steps: [], boundary_message_id: 20 };
const message = (id, role, purpose, status = "completed", extra = {}) => ({
  id, role, status, attempt: 1, request_id: purpose === "mock_question" ? "question" : "answer",
  content: role === "user" ? "My own saved answer" : "Saved substantive feedback",
  workflow: { run_id: run.run_id, step: 0, step_revision: 1, purpose }, ...extra,
});
const question = message(22, "assistant", "mock_question");
const answer = message(23, "user", "mock_answer");
const feedback = message(24, "assistant", "mock_answer");
const render = (props = {}) => renderToStaticMarkup(React.createElement(MockInterviewGuide, {
  messages: [], busy: false, start() {}, send: async () => true,
  update: async () => true, retry() {}, ...props,
}));

test("question-only and failed feedback never expose the advance action", () => {
  assert.deepEqual(workflowCommand("next stage"), { operation: "advance" });
  assert.deepEqual(workflowCommand("下一步"), { operation: "advance" });
  assert.equal(mockInterviewProgress(run, []).phase, "prepare");
  assert.equal(mockInterviewProgress(run, [question]).phase, "answer");
  assert.equal(mockInterviewProgress(run, [question, answer, { ...feedback, status: "generating" }]).phase, "feedback");
  assert.equal(mockInterviewProgress(run, [question, answer, { ...feedback, status: "failed" }]).phase, "failed");
  assert.equal(mockInterviewProgress(run, [question, answer, feedback]).phase, "ready");
  const waiting = render({ run, messages: [question] });
  assert.match(waiting, /提交回答/);
  assert.match(waiting, /目标岗位和希望练习的方向/);
  assert.doesNotMatch(waiting, /进入自我介绍/);
  const ready = render({ run, messages: [question, answer, feedback] });
  assert.match(ready, /进入自我介绍/);
  assert.doesNotMatch(ready, /<textarea/);
});

test("refresh scopes evidence to run, step version and message boundary", () => {
  for (const changed of [{ run_id: "old-run" }, { step_revision: 8 }, { current: 1 }, { boundary_message_id: 24 }]) {
    assert.equal(mockInterviewProgress({ ...run, ...changed }, [question, answer, feedback]).phase, "prepare");
  }
  const paused = render({ run: { ...run, status: "paused" }, messages: [question, answer, feedback] });
  assert.match(paused, /继续面试/);
  assert.doesNotMatch(paused, /进入自我介绍/);
});

test("review waits for persisted report without requesting another answer", () => {
  const reviewing = { ...run, current: 5, step_revision: 9, boundary_message_id: 24, skipped: [2], completed_steps: [0, 1, 3, 4] };
  const report = message(26, "assistant", "mock_report", "generating", { workflow: {
    run_id: run.run_id, step: 5, step_revision: 9, purpose: "mock_report",
  } });
  const pending = render({ run: reviewing, messages: [report] });
  assert.match(pending, /正在根据本次已保存的回答/);
  assert.doesNotMatch(pending, /结束面试，查看复盘|<textarea/);
  const saved = render({ run: reviewing, messages: [{ ...report, status: "completed" }] });
  assert.match(saved, /结束面试，查看复盘/);
  assert.match(saved, /已跳过/);
  assert.doesNotMatch(saved, /<textarea/);
});

test("first use and resumed interview render English through useLocale", () => {
  setLocale("en");
  try {
    assert.match(render(), /Start interview preparation/);
    const english = render({ run, messages: [question] });
    assert.match(english, /Submit answer/);
    assert.match(english, /target role and practice goals/);
    assert.doesNotMatch(english, /提交回答/);
  } finally { setLocale("zh-CN"); }
});
