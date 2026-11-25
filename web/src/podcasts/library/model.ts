import type { PodcastEpisode, PodcastLanguage, PodcastLibrary } from "../../api/podcasts";

export const podcastLanguages = {
  zh: "中文",
  en: "英文",
  bilingual: "中英双语",
} satisfies Record<PodcastLanguage, string>;

export const defaultPodcastLanguage: PodcastLanguage = "zh";

export function isPodcastLanguage(value: string): value is PodcastLanguage {
  return Object.prototype.hasOwnProperty.call(podcastLanguages, value);
}

export function episodeLanguageLabel(episode: PodcastEpisode) {
  return podcastLanguages[episode.language ?? defaultPodcastLanguage];
}

export const episodeStatus = {
  generating: "正在生成讲稿",
  draft: "待确认讲稿",
  synthesizing: "正在合成音频",
  ready: "可播放",
  script_failed: "讲稿生成失败",
  audio_failed: "音频合成失败",
} satisfies Record<PodcastEpisode["status"], string>;

export const familiarityLabel = {
  unmarked: "未标记",
  familiar: "熟悉",
  unfamiliar: "不熟悉",
} satisfies Record<PodcastEpisode["familiarity"], string>;

export function isProducing(episode: PodcastEpisode) {
  return episode.status === "generating" || episode.status === "synthesizing";
}

export function canPreview(episode: PodcastEpisode) {
  return ["draft", "synthesizing", "ready", "audio_failed"].includes(episode.status);
}

export function parseQuestionIds(value: string | null) {
  return [
    ...new Set(
      (value ?? "")
        .split(",")
        .map((part) => part.trim())
        .filter((part) => /^\d+$/.test(part))
        .map(Number)
        .filter((id) => Number.isInteger(id) && id > 0 && id <= 2_147_483_647),
    ),
  ];
}

export function searchEpisodes(episodes: PodcastEpisode[], query: string) {
  const terms = query.trim().toLowerCase().split(/\s+/);
  return episodes.filter(({ title, question_code, topic }) =>
    terms.every((term) => `${title} ${question_code} ${topic}`.toLowerCase().includes(term)),
  );
}

export function playlistEpisodes(library: PodcastLibrary, ids: number[]) {
  const byId = new Map(library.episodes.map((episode) => [episode.id, episode]));
  return ids.flatMap((id) => {
    const episode = byId.get(id);
    return episode ? [episode] : [];
  });
}

export function importedEpisodes(
  episodes: PodcastEpisode[],
  batch: {
    ids: number[];
    questionIds: number[];
  },
) {
  const returned = new Set(batch.ids);
  const sources = new Set(episodes.filter((e) => returned.has(e.id)).map((e) => e.source_id));
  const questions = new Map(batch.questionIds.map((id, index) => [id, index]));
  return episodes
    .filter(
      (episode) =>
        returned.has(episode.id) ||
        (episode.source_id != null && sources.has(episode.source_id)),
    )
    .sort(
      (a, b) =>
        (questions.get(a.question_id) ?? 0) - (questions.get(b.question_id) ?? 0) ||
        a.part - b.part,
    );
}

export function readyEpisodes(episodes: PodcastEpisode[]) {
  return episodes.filter((episode) => episode.status === "ready");
}

export function moveEpisode(ids: number[], id: number, direction: -1 | 1) {
  const index = ids.indexOf(id);
  const destination = index + direction;
  if (index < 0 || destination < 0 || destination >= ids.length) return ids;
  const next = [...ids];
  next.splice(index, 1);
  next.splice(destination, 0, id);
  return next;
}

export function duration(seconds: number) {
  const whole = Math.max(0, Math.floor(seconds));
  return [Math.floor(whole / 60), whole % 60]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
}

export function errorDetail(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? `${fallback}（${error.message}）` : fallback;
}
