import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement as h } from "react";
import { act, create } from "react-test-renderer";
import { api } from "../src/api/client";
import { podcastApi } from "../src/api/podcasts";
import { EpisodeList } from "../src/podcasts/library/EpisodeList";
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
  source_id: id,
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
  const second = { ...episode(9, 91, "顺序选择", "draft", 2), source_id: draft.source_id };
  const otherLanguage = { ...episode(11, 91, "同步生成的英文版", "draft", 2), language: "en" };
  assert.deepEqual(
    importedEpisodes([reuse, second, draft, otherLanguage, episode(3, 91, "旧节目"), episode(10, 2, "其他题")], {
      ids: [8, 4],
      questionIds: [91, 24],
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

test("multi-question language and selection survive pagination and submit with series and playlist", async (t) => {
  const pages = [];
  const imported = [];
  const submitted = [];
  t.mock.method(podcastApi, "sources", async (_query, offset) => {
    pages.push(offset);
    return {
      items: [{ id: 21 + offset, title: "下一道题", topic: "Agent 工程", question_code: "Q-1" }],
      total: 28,
    };
  });
  t.mock.method(api, "post", async (path, body) => {
    submitted.push([path, JSON.parse(JSON.stringify(body))]);
    return { episode_ids: [40], skipped_question_ids: [18] };
  });

  const root = render(
    t,
    h(PodcastImport, {
      initialIds: [18],
      playlists: [{ id: 6, name: "路上", episode_ids: [] }],
      onClose() {},
      onImported: (...args) => imported.push(args),
    }),
  );
  const language = root.findByProps({ "aria-describedby": "podlib-bilingual-description" });
  assert.equal(language.props.value, "zh");
  assert.deepEqual(language.findAllByType("option").map(({ props }) => props.value), [
    "zh", "en", "bilingual",
  ]);
  assert.match(root.findByProps({ id: "podlib-bilingual-description" }).children.join(""),
    /中文讲解和英文面试回答/);
  act(() => language.props.onChange({ target: { value: "bilingual" } }));
  const flush = async () => {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 12));
    });
  };
  await flush();
  act(() => labelButton(root, "下一页").props.onClick());
  await flush();
  assert.deepEqual(pages, [0, 15]);
  assert.equal(language.props.value, "bilingual");
  const checkboxes = root.findAllByProps({ type: "checkbox" });
  act(() => checkboxes[0].props.onChange({ target: { checked: true } }));
  act(() => checkboxes[1].props.onChange({ target: { checked: true } }));
  act(() => root.findAllByType("select")[1].props.onChange({ target: { value: "6" } }));
  await act(async () => root.findByType("form").props.onSubmit({ preventDefault() {} }));
  assert.deepEqual(submitted, [["/podcasts/import", {
    question_ids: [18, 36], series: true, playlist_id: 6, language: "bilingual",
  }]]);
  assert.deepEqual(imported, [[[40], [18, 36], 1]]);
});

for (const language of ["zh", "en"]) {
  test(`single-question import sends ${language} and keeps the existing series and playlist defaults`, async (t) => {
    const post = t.mock.method(api, "post", async () => ({ episode_ids: [40] }));
    t.mock.method(podcastApi, "sources", async () => ({ items: [], total: 0 }));
    const root = render(t, h(PodcastImport, {
      initialIds: [18], playlists: [], onClose() {}, onImported() {},
    }));
    const select = root.findByProps({ "aria-describedby": "podlib-bilingual-description" });
    assert.equal(select.props.value, "zh");
    if (language === "en") act(() => select.props.onChange({ target: { value: "en" } }));
    await act(async () => root.findByType("form").props.onSubmit({ preventDefault() {} }));

    const [path, body] = post.mock.calls[0].arguments;
    assert.equal(path, "/podcasts/import");
    assert.deepEqual(JSON.parse(JSON.stringify(body)), { question_ids: [18], series: false, language });
  });
}

test("library labels explicit episode languages and treats old episodes as Chinese", (t) => {
  const root = render(t, h(EpisodeList, {
    episodes: [
      episode(1, 1, "旧节目"),
      { ...episode(2, 2, "英文节目"), language: "en" },
      { ...episode(3, 3, "双语节目"), language: "bilingual" },
    ],
    onPlay() {},
  }));
  assert.deepEqual(root.findAllByType("small").map(({ children }) => children.at(-1)), [
    "Q-1 · Agent 工程 · 中文",
    "Q-2 · Agent 工程 · 英文",
    "Q-3 · Agent 工程 · 中英双语",
  ]);
});

test("import API supports callers that omit language", async (t) => {
  const post = t.mock.method(api, "post", async () => ({ episode_ids: [40] }));
  await podcastApi.importQuestions([18], false, 6);
  const [, body] = post.mock.calls[0].arguments;
  assert.deepEqual(JSON.parse(JSON.stringify(body)), {
    question_ids: [18], series: false, playlist_id: 6,
  });
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
        { ...episode(5, 1, "第二集", "draft", 2), language: "en" },
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
  assert.deepEqual(root.findByType("ul").findAllByType("small").map(({ children }) => children[0]), [
    "Q-1 · 第 1 集 · 中文", "Q-1 · 第 2 集 · 英文", "Q-2 · 第 1 集 · 中文",
  ]);
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
  assert.equal(root.findByType("article").findByType("p").children.join(""), "第 2 集 · 英文");
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
