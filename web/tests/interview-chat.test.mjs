import assert from "node:assert/strict";
import test from "node:test";
import { parsePrompt, positiveId, roles, workflows } from "../src/interview/chat/model.ts";

test("a role command keeps the original natural language, without the command prefix", () => {
  assert.deepEqual(parsePrompt("/resume 帮我分析这个项目", null), {
    kind: "message",
    content: "帮我分析这个项目",
    role: "resume",
    intent: null,
  });
  assert.deepEqual(parsePrompt("/exam", null), { kind: "select", role: "exam", intent: "exam" });
  assert.equal(parsePrompt("/unknown 为什么", null).content, "/unknown 为什么");
  assert.equal(roles.length, 6);
  assert.equal(workflows.length, 6);
});

test("recognized intents offer cards while retaining the user request", () => {
  for (const [content, intent] of [
    ["展示我的能力差距", "benchmark"],
    ["从题库抽5道Redis题", "questions"],
    ["帮我组一套模拟试卷", "exam"],
    ["看看今天的学习计划", "plan"],
    ["开始一道随堂测验", "quiz"],
  ]) {
    const parsed = parsePrompt(content, null);
    assert.equal(parsed.content, content);
    assert.equal(parsed.intent, intent);
  }
  assert.equal(parsePrompt("考试进程如何实现隔离？", null).intent, null);
});

test("route IDs are strictly positive safe integers", () => {
  for (const value of ["", "0", "01", "-1", "1.5", "1e2", "9007199254740992", null]) {
    assert.equal(positiveId(value), undefined);
  }
  assert.equal(positiveId("23"), 23);
});
