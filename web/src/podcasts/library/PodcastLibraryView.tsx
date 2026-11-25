import { useState } from "react";
import type { PodcastLibrary } from "../../api/podcasts";
import type { StartPodcastPlayback } from "../podcastQueue";
import { EpisodeList } from "./EpisodeList";
import { duration, playlistEpisodes, readyEpisodes, searchEpisodes } from "./model";
import { PodcastCover, PodcastHeading, PodcastIcon } from "./PodcastUi";

export function PodcastLibraryView({
  library,
  loading,
  onImport,
  onProduction,
  onNew,
  onPlaylist,
  onTopic,
  onPlay,
  onOpenPlayer,
  onAdd,
  onDelete,
}: {
  library: PodcastLibrary;
  loading: boolean;
  onImport: () => void;
  onProduction: () => void;
  onNew: () => void;
  onPlaylist: (id: number) => void;
  onTopic: (topic: string) => void;
  onPlay: StartPodcastPlayback;
  onOpenPlayer: () => void;
  onAdd: (id: number) => void;
  onDelete: (id: number) => void;
}) {
  const [query, setQuery] = useState("");
  const [openMenu, setOpenMenu] = useState<number>();
  const [showTopics, setShowTopics] = useState(false);
  const [shuffleMessage, setShuffleMessage] = useState(false);
  const ready = readyEpisodes(library.episodes);
  const jobs = library.episodes.filter((episode) => episode.status !== "ready");
  const visible = searchEpisodes(ready, query);
  const resumed = ready.find(
    (episode) =>
      episode.position_seconds > 0 && episode.position_seconds < episode.duration_seconds,
  );
  const recent = resumed ?? ready[0];
  const topics = [...new Set(ready.map((episode) => episode.topic).filter(Boolean))];
  const hasUnfamiliar = ready.some((episode) => episode.familiarity === "unfamiliar");

  function shuffle() {
    const queue = ready.filter((episode) => episode.familiarity === "unfamiliar");
    if (!queue.length) {
      setShuffleMessage(true);
      return;
    }
    setShuffleMessage(false);
    const first = queue[Math.floor(Math.random() * queue.length)];
    onPlay(first, queue, { kind: "unfamiliar" });
  }

  return (
    <>
      <PodcastHeading title="面试播客" sub="把面试题放进耳机里，接着上次听。">
        <button type="button" className="podlib-button podlib-primary" onClick={onImport}>
          <PodcastIcon name="plus" />
          从题库导入
        </button>
      </PodcastHeading>
      {recent && (
        <section className="podlib-hero" aria-label={resumed ? "继续收听" : "最近加入"}>
          <div className="podlib-hero-content">
            <p className="podlib-hero-label">
              <PodcastIcon name="headphones" />
              {resumed ? "继续收听" : "最近加入"}
            </p>
            <h2>{recent.title}</h2>
            <p className="podlib-hero-meta">
              {[
                recent.question_code,
                recent.topic,
                resumed
                  ? `已听 ${duration(recent.position_seconds)}，共 ${duration(recent.duration_seconds)}`
                  : duration(recent.duration_seconds),
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
            <button
              type="button"
              className="podlib-button podlib-resume"
              onClick={() => onPlay(recent, ready, { kind: "all" })}
            >
              <PodcastIcon name="play" />
              {resumed ? `从 ${duration(recent.position_seconds)} 继续` : "播放这集"}
            </button>
          </div>
          <PodcastCover />
        </section>
      )}
      <div className="podlib-mobile-actions">
        <button type="button" className="podlib-button podlib-tint" onClick={shuffle}>
          <PodcastIcon name="shuffle" />
          不熟悉的随机听
        </button>
        <button type="button" className="podlib-button podlib-outline" onClick={onOpenPlayer}>
          <PodcastIcon name="settings" />
          播放设置
        </button>
        {shuffleMessage && !hasUnfamiliar && <p role="status">还没有标记为不熟悉的节目。</p>}
      </div>

      {jobs.length > 0 && (
        <section className="podlib-panel podlib-retained">
          <div>
            <h2>制作记录</h2>
            <p>{jobs.length} 集待确认、处理中或需要重试</p>
          </div>
          <button type="button" className="podlib-button podlib-tint" onClick={onProduction}>
            查看制作进度
            <PodcastIcon name="chevron" />
          </button>
        </section>
      )}
      <div className="podlib-grid">
        <section className="podlib-panel podlib-library-list">
          <div className="podlib-panel-head podlib-divider">
            <h2>全部节目</h2>
            <label className="podlib-search">
              <PodcastIcon name="search" />
              <input
                type="search"
                aria-label="搜索节目"
                placeholder="搜索节目"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setOpenMenu(undefined);
                }}
              />
            </label>
          </div>
          {loading ? (
            <p role="status">正在加载节目库…</p>
          ) : visible.length > 0 ? (
            <EpisodeList
              episodes={visible}
              onPlay={(episode) => onPlay(
                episode,
                visible,
                query.trim()
                  ? { kind: "selection", source: "search", episodeIds: visible.map((item) => item.id) }
                  : { kind: "all" },
              )}
              menu={{
                openId: openMenu,
                onToggle: (id) => setOpenMenu((open) => (open === id ? undefined : id)),
                onAdd: (id) => {
                  setOpenMenu(undefined);
                  onAdd(id);
                },
                onDelete: (id) => {
                  setOpenMenu(undefined);
                  onDelete(id);
                },
              }}
            />
          ) : (
            <p>{query.trim() ? "没有找到符合条件的节目。" : "还没有可播放的节目。"}</p>
          )}
        </section>
        <aside className="podlib-panel podlib-playlists">
          <div className="podlib-panel-head">
            <h2>我的歌单</h2>
            <button type="button" className="podlib-button podlib-tint" onClick={onNew}>
              <PodcastIcon name="plusPurple" />
              新建
            </button>
          </div>
          {library.playlists.length ? (
            <ul className="podlib-playlist-list">
              {library.playlists.map((playlist) => {
                const episodes = readyEpisodes(playlistEpisodes(library, playlist.episode_ids));
                const minutes = Math.round(
                  episodes.reduce((sum, episode) => sum + episode.duration_seconds, 0) / 60,
                );
                return (
                  <li key={playlist.id}>
                    <button
                      type="button"
                      className="podlib-nav-row"
                      onClick={() => onPlaylist(playlist.id)}
                    >
                      <PodcastCover />
                      <span>
                        <strong>{playlist.name}</strong>
                        <small>
                          {playlist.episode_ids.length} 集{minutes > 0 && ` · ${minutes} 分钟`}
                        </small>
                      </span>
                      <PodcastIcon name="chevron" />
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            !loading && <p className="podlib-note">还没有歌单。</p>
          )}
          <div className="podlib-shortcuts">
            <button
              type="button"
              className="podlib-nav-row podlib-desktop-shuffle"
              onClick={shuffle}
            >
              <PodcastIcon name="shuffle" />
              <span>
                <strong>不熟悉的随机听</strong>
                <small>{hasUnfamiliar ? "仅播放你亲手标记过的节目" : "标记不熟悉后可听"}</small>
              </span>
              <PodcastIcon name="chevron" />
            </button>
            {shuffleMessage && !hasUnfamiliar && (
              <p className="podlib-desktop-shuffle podlib-note" role="status">
                还没有标记为不熟悉的节目。
              </p>
            )}
            <button
              type="button"
              className="podlib-nav-row"
              onClick={() => setShowTopics((value) => !value)}
              aria-expanded={showTopics}
            >
              <PodcastIcon name="folder" />
              <span>
                <strong>按主题播放</strong>
                {topics.length > 0 && <small>{topics.length} 个主题</small>}
              </span>
              <PodcastIcon name="chevron" />
            </button>
            {showTopics &&
              (topics.length ? (
                <ul className="podlib-choice-topics">
                  {topics.map((topic) => (
                    <li key={topic}>
                      <button
                        type="button"
                        className="podlib-button podlib-tint"
                        onClick={() => onTopic(topic)}
                      >
                        {topic}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="podlib-note">还没有可播放的主题。</p>
              ))}
          </div>
        </aside>
      </div>
    </>
  );
}
