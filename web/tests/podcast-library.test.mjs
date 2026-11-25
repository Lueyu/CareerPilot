import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement as h } from "react";
import { act, create } from "react-test-renderer";
import { podcastApi } from "../src/api/podcasts";
import { PodcastImport } from "../src/podcasts/library/PodcastImport";
import { PodcastProduction } from "../src/podcasts/library/PodcastProduction";
import {
  importedEpisodes,
  moveEpisode,
  parseQuestionIds,
  playlistEpisodes,
  readyEpisodes,
  searchEpisodes,
} from "../src/podcasts/library/model";

const episode = (id, question_id, title, status = "ready", part = 1) => ({
  id,
  question_id,
  title,
  status,
  part,
  question_code: "Q-" + question_id,
  topic: "Agent 工程",
  duration_seconds: 140,
  position_seconds: 0,
  familiarity: "unmarked",
  error: null,
});

test("deep links accept unique bank IDs and search combines title, question code and topic", () => {
  assert.deepEqual(parseQuestionIds("18, 2,18,0,1e2,-5,2147483648,invalid"), [18, 2]);
  const safe = episode(1, 18, "发券超时");
  assert.deepEqual(searchEpisodes([safe, episode(2, 19, "超时重试")], "q-18  Agent"), [safe]);
});

test("batch includes delayed series parts and reused IDs without importing old versions or other questions", () => {
  const draft = episode(8, 91, "工具调用", "generating");
  const reuse = episode(4, 24, "超时后的处理");
  const second = episode(9, 91, "顺序选择", "draft", 2);
  assert.deepEqual(
    importedEpisodes([reuse, second, draft, episode(3, 91, "旧节目"), episode(10, 2, "其他题")], {
      ids: [8, 4],
      questionIds: [91, 24],
      previousIds: new Set([3, 4]),
    }),
    [draft, second, reuse],
  );
});

test("playlist order stays explicit and excludes unfinished episodes from playback", () => {
  const draft = episode(1, 1, "讲稿", "draft");
  const first = episode(2, 2, "第一集");
  const last = episode(3, 3, "最后一集");
  const ids = moveEpisode([2, 3, 1], 3, -1).filter((id) => id !== 1);
  assert.deepEqual(
    readyEpisodes(
      playlistEpisodes({ episodes: [draft, first, last], playlists: [] }, [...ids, 1, 99]),
    ),
    [last, first],
  );
});

function render(t, element) {
  const oldWindow = globalThis.window;
  globalThis.window = { matchMedia: () => ({ matches: false }), setTimeout, clearTimeout };
  let renderer;
  act(() => {
    renderer = create(element);
  });
  t.after(() => {
    act(() => renderer.unmount());
    globalThis.window = oldWindow;
  });
  return renderer.root;
}

function labelButton(root, label) {
  return root
    .findAllByType("button")
    .find(
      (node) =>
        node.children
          .filter((child) => typeof child === "string" || typeof child === "number")
          .join("") === label,
    );
}

test("source selection survives pagination and submits deep-link IDs with series, playlist and skipped count", async (t) => {
  const original = { sources: podcastApi.sources, importQuestions: podcastApi.importQuestions };
  t.after(() => Object.assign(podcastApi, original));
  const pages = [];
  const imported = [];
  const submitted = [];
  podcastApi.sources = async (_query, offset) => {
    pages.push(offset);
    return {
      items: [{ id: 21 + offset, title: "下一道题", topic: "Agent 工程", question_code: "Q-1" }],
      total: 28,
    };
  };
  podcastApi.importQuestions = async (...args) => {
    submitted.push(args);
    return { episode_ids: [40], skipped_question_ids: [18] };
  };

  const root = render(
    t,
    h(PodcastImport, {
      initialIds: [18],
      playlists: [{ id: 6, name: "路上", episode_ids: [] }],
      onClose() {},
      onImported: (...args) => imported.push(args),
    }),
  );
  const flush = async () => {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 12));
    });
  };
  await flush();
  act(() => labelButton(root, "下一页").props.onClick());
  await flush();
  assert.deepEqual(pages, [0, 15]);
  const checkboxes = root.findAllByProps({ type: "checkbox" });
  act(() => checkboxes[0].props.onChange({ target: { checked: true } }));
  act(() => checkboxes[1].props.onChange({ target: { checked: true } }));
  act(() => root.findByType("select").props.onChange({ target: { value: "6" } }));
  await act(async () => root.findByType("form").props.onSubmit({ preventDefault() {} }));
  assert.deepEqual(submitted, [[[18, 36], true, 6]]);
  assert.deepEqual(imported, [[[40], [18, 36], 1]]);
});

test("reading preview exposes one draft on demand, confirms only selected parts and retries just the failed episode", async (t) => {
  const original = podcastApi.script;
  t.after(() => {
    podcastApi.script = original;
  });
  const scripts = [];
  const synthesized = [];
  const retried = [];
  const deleted = [];
  podcastApi.script = async (id) => {
    scripts.push(id);
    return { title: "第二集讲稿", script: "可读讲稿" };
  };
  const root = render(
    t,
    h(PodcastProduction, {
      episodes: [
        episode(4, 1, "第一集", "draft"),
        episode(5, 1, "第二集", "draft", 2),
        episode(8, 2, "失败集", "audio_failed"),
      ],
      waiting: false,
      busy: false,
      missingQuestions: 1,
      onBack() {},
      onPlay() {},
      onRetry: (id) => retried.push(id),
      onDelete: (id) => deleted.push(id),
      onSynthesize: async (episodes) => {
        synthesized.push(episodes.map((item) => item.id));
      },
    }),
  );
  assert.deepEqual(scripts, []);
  act(() => root.findByProps({ "aria-label": "删除节目，失败集" }).props.onClick());
  assert.deepEqual(deleted, [8]);
  await act(async () =>
    root
      .findAllByType("button")
      .filter((node) => node.children.includes("预览讲稿"))[1]
      .props.onClick({ currentTarget: null }),
  );
  assert.deepEqual(scripts, [5]);
  assert.equal(root.findByType("article").findByType("h2").children[0], "第二集讲稿");
  assert.match(root.findByProps({ role: "alert" }).children.join(""), /1 道所选题目/);
  act(() =>
    root
      .findByProps({ "aria-label": "合成 第二集 第 2 集" })
      .props.onChange({ target: { checked: true } }),
  );
  act(() => labelButton(root, "确认并合成 1 集").props.onClick());
  assert.deepEqual(synthesized, []);
  await act(async () => labelButton(root, "合成选中的节目").props.onClick());
  assert.deepEqual(synthesized, [[5]]);
  act(() => labelButton(root, "只重试这一集").props.onClick());
  assert.deepEqual(retried, [8]);
});
