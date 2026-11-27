import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { setLocale } from "../src/i18n.ts";
import { detectToolIntent, demoSample, emptyDemoMemory } from "../src/interview/chat/tools/model.ts";
import { toolCopy, toolCopyKeys, toolReply } from "../src/interview/chat/tools/copy.ts";
import {
  DEMO_CONTEXT_RESET_EVENT,
  isDemoContextReset,
  resetDemoContext,
  restoreDemoMemory,
} from "../src/interview/chat/tools/contextStore.ts";
import { DemoToolLayer } from "../src/interview/chat/tools/ToolCardView.tsx";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");
const toolSource = (name) => read("../src/interview/chat/tools/" + name);

const intents = [
  "search",
  "parse",
  "sandbox",
  "mindmap",
  "calendar",
  "translate",
  "diff",
  "skill",
];

test("demo intent detection matches explicit tool requests only", () => {
  assert.equal(detectToolIntent(""), null);
  assert.equal(detectToolIntent("   "), null);
  assert.equal(detectToolIntent("搜一下 Redis 持久化面经"), "search");
  assert.equal(detectToolIntent("Search for Redis persistence interview notes"), "search");
  assert.equal(detectToolIntent("帮我翻译这段文字"), "translate");
  assert.equal(detectToolIntent("polish this sentence"), "translate");
  assert.equal(detectToolIntent("画一张思维导图"), "mindmap");
  assert.equal(detectToolIntent("解析一下简历"), "parse");
  assert.equal(detectToolIntent("设置学习提醒"), "calendar");
  assert.equal(detectToolIntent("看看技能进度"), "skill");
  assert.equal(detectToolIntent("版本对比"), "diff");
  assert.equal(detectToolIntent("运行这段代码"), "sandbox");
  // 真实提问不该被演示层吞掉。
  assert.equal(detectToolIntent("请按这道题评价我的作答"), null);
  assert.equal(detectToolIntent("我的项目该怎么讲"), null);
  assert.equal(detectToolIntent("开始一道随堂测验"), null);
});

test("tool copy dictionaries stay aligned and interpolate values", async () => {
  const source = await toolSource("copy.ts");
  const keys = (name) => {
    const block = source.match(
      new RegExp("const " + name + ": Record<string, string> = \\{([\\s\\S]*?)\\r?\\n\\};"),
    );
    assert.ok(block, name + " dictionary not found");
    return [...block[1].matchAll(/^\s*"([^"]+)":/gm)].map((match) => match[1]).sort();
  };
  const zh = keys("zh");
  assert.deepEqual(keys("en"), zh);
  assert.deepEqual(zh, [...toolCopyKeys].sort());
  assert.ok(toolCopyKeys.length > 100);
  assert.equal(toolCopy("zh-CN")("search.count", { count: 3 }), "共 3 条示例");
  assert.equal(toolCopy("en")("memory.count", { count: 3 }), "3 · sample data");
  assert.throws(() => toolCopy("zh-CN")("not.a.real.key"), /Unknown tool copy key/);
});

test("demo replies never claim a real capability", () => {
  for (const intent of intents) {
    assert.match(toolReply(intent, "zh-CN"), /演示/);
    assert.match(toolReply(intent, "en"), /demo/i);
  }
  assert.match(toolCopy("zh-CN")("reply.search"), /未联网/);
  assert.match(toolCopy("en")("reply.sandbox"), /No code was executed/);
});

test("sample data is marked as sample and reset only touches demo state", () => {
  assert.deepEqual(emptyDemoMemory(), {
    targetRole: null,
    weakPoints: [],
    currentSkill: null,
    examCount: 0,
  });
  assert.match(demoSample("zh-CN").memory.targetRole, /示例/);
  assert.match(demoSample("en").memory.targetRole, /Sample/);
  assert.equal(DEMO_CONTEXT_RESET_EVENT, "interview-demo-context-reset");
  restoreDemoMemory();
  assert.equal(isDemoContextReset(), false);
  resetDemoContext();
  assert.equal(isDemoContextReset(), true);
  restoreDemoMemory();
  assert.equal(isDemoContextReset(), false);
});

test("the demo layer is labelled, read-only and bilingual", () => {
  setLocale("zh-CN");
  const zh = renderToStaticMarkup(React.createElement(DemoToolLayer));
  assert.match(zh, /演示数据/);
  assert.match(zh, /演示回复/);
  assert.match(zh, /这是搜索工具卡的演示结果，未联网。/);
  assert.match(zh, /演示卡片内容来自固定示例数据/);
  assert.match(zh, /保存（演示不可写）/);
  assert.match(zh, /编辑（未接入）/);
  assert.match(zh, /演示模式下所有写操作均为禁用态/);
  assert.match(zh, /disabled=""/);
  assert.doesNotMatch(zh, /查看会话记忆/);
  setLocale("en");
  const en = renderToStaticMarkup(React.createElement(DemoToolLayer));
  assert.match(en, /DEMO DATA/);
  assert.match(en, /DEMO REPLY/);
  assert.match(en, /No live search was performed/);
  assert.match(en, /no network request was made/i);
  setLocale("zh-CN");
});

test("personal workspace keeps real controls and never renders demo cards", async () => {
  const chat = await read("../src/interview/chat/ChatWorkspace.tsx");
  assert.match(chat, /const isDemo = getWorkspace\(\) === "demo";/);
  assert.match(chat, /\{isDemo && <DemoToolLayer \/>\}/);
  assert.doesNotMatch(chat, /detectToolIntent/);
  assert.match(chat, /mockInterviewProgress\(run, messages\)/);
  const shell = await read("../src/interview/InterviewShell.tsx");
  assert.match(shell, /<LanguageSelector compact \/>/);
  assert.match(shell, /className="ia-memory-indicator"/);
  assert.match(shell, /overview\?\.counts\?\.memory/);
  assert.match(shell, /\{isDemo \|\| overview\?\.workspace\?\.is_demo/);
  assert.match(shell, /useLocale\(\)/);
  assert.doesNotMatch(shell, /MemoryIndicator/);
  const layer = await toolSource("ToolCardView.tsx");
  assert.match(layer, /DemoBanner action=\{<MemoryIndicator \/>\}/);
});

test("unwired actions are disabled and the tool layer has no writes or new icon package", async () => {
  const voice = await toolSource("Voice.tsx");
  assert.ok((voice.match(/\bdisabled\b/g) ?? []).length >= 2);
  assert.match(voice, /aria-disabled="true"/);
  const ui = await toolSource("ui.tsx");
  assert.match(ui, /export function DemoDisabledButton/);
  assert.match(ui, /\bdisabled\b/);
  assert.match(ui, /export function ToolIcon/);
  const pkg = JSON.parse(await read("../package.json"));
  assert.equal(pkg.dependencies?.["lucide-react"], undefined);
  assert.equal(pkg.devDependencies?.["lucide-react"], undefined);
  for (const name of [
    "Context.tsx",
    "KnowledgeCards.tsx",
    "SkillProgressCard.tsx",
    "TextCards.tsx",
    "ToolCards.tsx",
    "ToolCardView.tsx",
    "Voice.tsx",
    "ui.tsx",
    "copy.ts",
    "model.ts",
    "contextStore.ts",
  ]) {
    const text = await toolSource(name);
    assert.doesNotMatch(
      text,
      /lucide-react|fetch\(|localStorage\.setItem|api\.(?:get|post|patch|put|delete)\(/,
      name + " must not write to the backend or add an icon package",
    );
  }
});

test("reset wording never claims to clear real context, memory or evidence", async () => {
  const copy = await toolSource("copy.ts");
  assert.match(copy, /真实记录保留/);
  assert.match(copy, /Real records are kept/);
  assert.match(copy, /真实记忆不受影响/);
  assert.match(copy, /Your real memory is unaffected/);
  assert.match(copy, /演示层不会生成回复/);
  assert.match(copy, /generates no reply/);
  assert.doesNotMatch(copy, /清除真实上下文|清空真实记忆|clear real context|delete real memor/i);
});
