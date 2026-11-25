import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getWorkspace } from "../../api/client";
import { podcastApi, type PodcastLibrary } from "../../api/podcasts";
import { EpisodeList } from "../../podcasts/library/EpisodeList";
import { PodcastImport } from "../../podcasts/library/PodcastImport";
import { PodcastLibraryView } from "../../podcasts/library/PodcastLibraryView";
import { PodcastPlaylistEditor } from "../../podcasts/library/PodcastPlaylistEditor";
import { PodcastProduction, PodcastScript } from "../../podcasts/library/PodcastProduction";
import {
  importedEpisodes,
  parseQuestionIds,
  playlistEpisodes,
  readyEpisodes,
  type ImportBatch,
} from "../../podcasts/library/model";
import {
  adjacentEpisode,
  scopeEpisodes,
  shuffleRound,
  type PlaybackScope,
  type StartPodcastPlayback,
} from "../../podcasts/podcastQueue";
import {
  currentPodcastIdentity,
  downloadPlaylist,
  saveOffline,
} from "../../podcasts/podcastOffline";
import { identityIsCurrent } from "../../podcasts/podcastOfflineIdentity";
import { PodcastDialog, podcastTime } from "../../podcasts/PodcastPlayerDialog";
import { usePodcastAudio } from "../../podcasts/usePodcastAudio";
import { errorText, QueryState, useAction, useRead } from "./ui";
import PodcastDeviceCopies from "./PodcastDeviceCopies";
import "../../pages/podcasts.css";
import "../../podcasts/podcastPlayer.css";

const all: PlaybackScope = { kind: "all" };
const empty: PodcastLibrary = { episodes: [], playlists: [] };
type View =
  | { kind: "library" | "import" }
  | { kind: "production"; batch?: ImportBatch }
  | { kind: "playlist" | "edit"; id: number }
  | { kind: "new"; episodeId?: number }
  | { kind: "topic"; topic: string };

export default function Podcasts() {
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<View>({ kind: "library" });
  const [showPlayer, setShowPlayer] = useState(false);
  const [device, setDevice] = useState(false);
  const [showScript, setShowScript] = useState(false);
  const [scope, setScope] = useState<PlaybackScope>(all);
  const [mode, setMode] = useState<"ordered" | "shuffle" | "repeat">("ordered");
  const [order, setOrder] = useState<number[]>([]);
  const [remove, setRemove] = useState<{ kind: "episode" | "playlist"; id: number }>();
  const [notice, setNotice] = useState("");
  const [progressError, setProgressError] = useState("");
  const positions = useRef(
    new Map<number, { value: number; saved: number | undefined; writing: boolean }>(),
  );
  const identity = useRef(currentPodcastIdentity());
  const action = useAction();
  const resource = useRead<PodcastLibrary>("/podcasts", 3500);
  const library = resource.data ?? empty;
  const questions = params.get("questions");
  const linkEpisode = params.get("episode");
  const screen = questions != null ? ({ kind: "import" } as const) : view;
  const activeBatch = screen.kind === "production" ? screen.batch : undefined;
  const batchEpisodes = activeBatch ? importedEpisodes(library.episodes, activeBatch) : [];
  const jobs = activeBatch
    ? batchEpisodes
    : library.episodes.filter((episode) => episode.status !== "ready");
  const ready = readyEpisodes(library.episodes);
  const eligible = scopeEpisodes(library, scope);
  const playlist =
    screen.kind === "playlist" || screen.kind === "edit"
      ? library.playlists.find((item) => item.id === screen.id)
      : undefined;
  const queueIds = mode === "shuffle" ? order : eligible.map((episode) => episode.id);

  function persistProgress(id: number, value: number) {
    if (!identityIsCurrent(identity.current) || getWorkspace() !== identity.current.workspace)
      return;
    const stored = positions.current.get(id) ?? { value, saved: undefined, writing: false };
    positions.current.set(id, stored);
    stored.value = Math.floor(value);
    if (stored.writing || stored.saved === stored.value) return;
    stored.writing = true;
    void (async () => {
      try {
        while (
          stored.saved !== stored.value &&
          identityIsCurrent(identity.current) &&
          getWorkspace() === identity.current.workspace
        ) {
          const next = stored.value;
          await podcastApi.update(id, { position_seconds: next });
          stored.saved = next;
        }
        setProgressError("");
      } catch (error) {
        setProgressError("播放进度保存失败。" + errorText(error));
      } finally {
        stored.writing = false;
      }
    })();
  }

  const audio = usePodcastAudio({
    load: podcastApi.audio,
    progress: persistProgress,
    ended: () => {
      if (mode === "repeat") {
        audio.seek(0);
        audio.play();
      } else step(1, true);
    },
  });
  const current =
    library.episodes.find((episode) => episode.id === audio.episode?.id) ?? audio.episode;
  const previous = adjacentEpisode(queueIds, eligible, current?.id, -1);
  const next = adjacentEpisode(queueIds, eligible, current?.id, 1);

  function step(direction: -1 | 1, autoplay: boolean) {
    const episode = direction === 1 ? next : previous;
    if (episode)
      audio.select(
        episode,
        positions.current.get(episode.id)?.value ?? episode.position_seconds,
        autoplay,
      );
  }

  const play: StartPodcastPlayback = (episode, episodes, nextScope) => {
    const shuffled = nextScope.kind === "unfamiliar" || mode === "shuffle";
    if (nextScope.kind === "unfamiliar") setMode("shuffle");
    if (shuffled)
      setOrder(
        shuffleRound(
          episodes.map((item) => item.id),
          episode.id,
        ),
      );
    setScope(nextScope);
    setShowScript(false);
    setShowPlayer(true);
    audio.select(
      episode,
      positions.current.get(episode.id)?.value ?? episode.position_seconds,
      true,
    );
  };
  const linkedPlay = useRef(play);
  useEffect(() => {
    linkedPlay.current = play;
  });
  useEffect(() => {
    if (linkEpisode === null || !resource.data) return;
    const allReady = readyEpisodes(resource.data.episodes);
    const match = /^\d+$/.test(linkEpisode)
      ? allReady.find((episode) => episode.id === Number(linkEpisode))
      : undefined;
    setParams({}, { replace: true });
    if (match) linkedPlay.current(match, allReady, all);
    else setNotice("该节目暂不可播放。");
  }, [linkEpisode, resource.data, setParams]);

  function go(nextView: View) {
    setView(nextView);
    if (questions !== null) setParams({}, { replace: true });
    setNotice("");
  }

  function mutate(change: () => Promise<unknown>, success?: string) {
    return action.run(async () => {
      try {
        await change();
        if (success) setNotice(success);
      } finally {
        resource.reload();
      }
    });
  }

  const playlistItems = playlist
    ? readyEpisodes(playlistEpisodes(library, playlist.episode_ids))
    : [];
  const topicItems =
    screen.kind === "topic" ? ready.filter((episode) => episode.topic === screen.topic) : [];
  const selectableScopes: { label: string; scope: PlaybackScope }[] = [
    { label: "全部节目", scope: all },
    { label: "不熟悉", scope: { kind: "unfamiliar" } },
    ...library.playlists.map((item) => ({
      label: item.name,
      scope: { kind: "playlist" as const, id: item.id },
    })),
    ...[...new Set(ready.map((episode) => episode.topic).filter(Boolean))].map((topic) => ({
      label: topic,
      scope: { kind: "topic" as const, topic },
    })),
    ...(scope.kind === "selection" ? [{ label: "当前所选节目", scope }] : []),
  ];
  function changeMode(value: typeof mode) {
    setMode(value);
    if (value === "shuffle")
      setOrder(
        shuffleRound(
          eligible.map((episode) => episode.id),
          current?.id,
        ),
      );
  }

  return (
    <div className="ial-podcasts podcasts">
      <QueryState {...resource} retry={resource.reload} />
      {!showPlayer && <QueryState error={action.error} />}
      {notice && <p role="status">{notice}</p>}
      {device && (
        <PodcastDeviceCopies identity={identity.current} onBack={() => setDevice(false)} />
      )}
      {!device && screen.kind === "library" && (
        <PodcastLibraryView
          library={library}
          loading={resource.loading || !!resource.error}
          onImport={() => go({ kind: "import" })}
          onProduction={() => go({ kind: "production" })}
          onNew={() => go({ kind: "new" })}
          onPlaylist={(id) => go({ kind: "playlist", id })}
          onTopic={(topic) => go({ kind: "topic", topic })}
          onPlay={play}
          onOpenPlayer={() => setShowPlayer(true)}
          onAdd={(episodeId) => go({ kind: "new", episodeId })}
          onDelete={(id) => setRemove({ kind: "episode", id })}
        />
      )}
      {!device && screen.kind === "import" && (
        <PodcastImport
          key={questions ?? "new"}
          initialIds={parseQuestionIds(questions)}
          playlists={library.playlists}
          onClose={() => go({ kind: "library" })}
          onImported={(batch) => {
            go({ kind: "production", batch });
            resource.reload();
          }}
        />
      )}
      {!device && screen.kind === "production" && (
        <PodcastProduction
          episodes={jobs}
          waiting={!!activeBatch?.ids.length && !batchEpisodes.length && resource.loading}
          missingQuestions={activeBatch?.skippedCount ?? 0}
          busy={action.busy}
          onBack={() => go({ kind: "library" })}
          onSynthesize={(episodes) =>
            mutate(async () => {
              const results = await Promise.allSettled(
                episodes.map((episode) => podcastApi.synthesize(episode.id)),
              );
              const failed = results.filter((result) => result.status === "rejected").length;
              if (failed) throw new Error(`${failed} 集未能加入合成队列，请检查制作状态后重试。`);
            }, "已提交音频合成。")
          }
          onRetry={(id) => void mutate(() => podcastApi.retry(id))}
          onDelete={(id) => setRemove({ kind: "episode", id })}
          onPlay={play}
        />
      )}
      {!device && (screen.kind === "new" || (screen.kind === "edit" && playlist)) && (
        <PodcastPlaylistEditor
          key={playlist?.id ?? "new"}
          library={library}
          playlist={playlist}
          startingEpisodeId={screen.kind === "new" ? screen.episodeId : undefined}
          busy={action.busy}
          onBack={() => go(playlist ? { kind: "playlist", id: playlist.id } : { kind: "library" })}
          onSave={(name, ids) =>
            void mutate(async () => {
              await (playlist
                ? podcastApi.updatePlaylist(playlist.id, { name, episode_ids: ids })
                : podcastApi.createPlaylist(name, ids));
              go({ kind: "library" });
            }, "歌单已保存。")
          }
          onDelete={() => playlist && setRemove({ kind: "playlist", id: playlist.id })}
        />
      )}
      {!device &&
        screen.kind === "new" &&
        screen.episodeId !== undefined &&
        !!library.playlists.length && (
          <div className="ial-panel ial-toolbar">
            <strong>加入现有歌单</strong>
            {library.playlists.map((item) => (
              <button
                className="ial-button"
                key={item.id}
                disabled={action.busy || item.episode_ids.includes(screen.episodeId!)}
                onClick={() =>
                  void mutate(async () => {
                    await podcastApi.updatePlaylist(item.id, {
                      episode_ids: [...item.episode_ids, screen.episodeId!],
                    });
                    go({ kind: "library" });
                  }, "已加入歌单。")
                }
              >
                {item.name}
              </button>
            ))}
          </div>
        )}
      {!device && (screen.kind === "playlist" || screen.kind === "topic") && (
        <section className="ial-panel">
          <div className="ial-toolbar">
            <button className="ial-button" onClick={() => go({ kind: "library" })}>
              返回节目库
            </button>
            <h2>{screen.kind === "topic" ? screen.topic : (playlist?.name ?? "歌单")}</h2>
            <span className="ial-spacer" />
            {playlist && (
              <button className="ial-button" onClick={() => go({ kind: "edit", id: playlist.id })}>
                管理歌单
              </button>
            )}
          </div>
          {screen.kind === "topic" ? (
            <EpisodeList
              episodes={topicItems}
              onPlay={(episode) =>
                play(episode, topicItems, { kind: "topic", topic: screen.topic })
              }
            />
          ) : (
            playlist && (
              <EpisodeList
                episodes={playlistItems}
                onPlay={(episode) =>
                  play(episode, playlistItems, { kind: "playlist", id: playlist.id })
                }
              />
            )
          )}
          {!(screen.kind === "topic" ? topicItems.length : playlistItems.length) && (
            <QueryState empty="当前范围暂无可播放节目。" />
          )}
        </section>
      )}
      {remove && (
        <PodcastDialog
          name="确认删除"
          open
          onClose={() => {
            if (!action.busy) setRemove(undefined);
          }}
        >
          <section className="ial-inset">
            <h2>{remove.kind === "episode" ? "删除这集播客？" : "删除此歌单？"}</h2>
            <p>
              {remove.kind === "episode"
                ? "这集节目将从节目库和歌单中移除。"
                : "歌单中的节目仍保存在节目库。"}
            </p>
            <QueryState error={action.error} />
            <div className="ial-actions">
              <button
                className="ial-button"
                disabled={action.busy}
                onClick={() => setRemove(undefined)}
              >
                取消
              </button>
              <button
                className="ial-button ial-primary"
                disabled={action.busy}
                onClick={() =>
                  void mutate(async () => {
                    if (remove.kind === "episode") {
                      await podcastApi.remove(remove.id);
                      if (current?.id === remove.id) audio.reset();
                    } else {
                      await podcastApi.removePlaylist(remove.id);
                      go({ kind: "library" });
                    }
                    setRemove(undefined);
                  }, "已删除。")
                }
              >
                确认删除
              </button>
            </div>
          </section>
        </PodcastDialog>
      )}
      <audio ref={audio.element} preload="auto" hidden />
      {!device && audio.episode && !showPlayer && (
        <aside className="ial-panel ial-player-bar" aria-label="播客迷你播放器">
          <button className="ial-button" onClick={() => setShowPlayer(true)}>
            {audio.episode.title} · {podcastTime(audio.position)}
          </button>
          <button
            className="ial-button"
            disabled={audio.loading}
            onClick={audio.playing ? audio.pause : audio.play}
          >
            {audio.playing ? "暂停" : "播放"}
          </button>
        </aside>
      )}
      <PodcastDialog name="播客播放器" open={showPlayer} onClose={() => setShowPlayer(false)}>
        <section className="ial-player">
          <div className="ial-toolbar">
            <h2>通勤播客</h2>
            <span className="ial-spacer" />
            <button className="ial-button" onClick={() => setShowPlayer(false)}>
              收起播放器
            </button>
          </div>
          {current && (
            <>
              <h3>{current.title}</h3>
              <label className="ial-player-progress">
                播放进度
                <input
                  type="range"
                  min="0"
                  step="0.25"
                  max={audio.duration}
                  value={Math.min(audio.position, audio.duration)}
                  disabled={!audio.duration}
                  onChange={(event) => audio.seek(Number(event.target.value))}
                />
              </label>
              <small>
                {podcastTime(audio.position)} / {podcastTime(audio.duration)}
              </small>
              <div className="ial-actions">
                <button className="ial-button" disabled={!previous} onClick={() => step(-1, true)}>
                  上一集
                </button>
                <button
                  className="ial-button ial-primary"
                  disabled={audio.loading}
                  onClick={audio.playing ? audio.pause : audio.play}
                >
                  {audio.playing ? "暂停" : "播放"}
                </button>
                <button className="ial-button" disabled={!next} onClick={() => step(1, true)}>
                  下一集
                </button>
              </div>
              <div className="ial-actions" role="group" aria-label="熟悉程度">
                {(["unfamiliar", "familiar"] as const).map((value) => (
                  <button
                    className="ial-button"
                    key={value}
                    aria-pressed={current.familiarity === value}
                    disabled={action.busy}
                    onClick={() =>
                      void mutate(() => podcastApi.update(current.id, { familiarity: value }))
                    }
                  >
                    {value === "familiar" ? "熟悉" : "不熟悉"}
                  </button>
                ))}
                <button
                  className="ial-button"
                  aria-expanded={showScript}
                  onClick={() => setShowScript((value) => !value)}
                >
                  逐字稿
                </button>
              </div>
              {showScript && <PodcastScript key={current.id} episode={current} />}
            </>
          )}
          <div className="ial-toolbar">
            <label>
              播放范围{" "}
              <select
                value={selectableScopes.findIndex(
                  (option) => JSON.stringify(option.scope) === JSON.stringify(scope),
                )}
                onChange={(event) => {
                  const selected = selectableScopes[Number(event.target.value)]?.scope ?? all;
                  setScope(selected);
                  if (mode === "shuffle")
                    setOrder(
                      shuffleRound(
                        scopeEpisodes(library, selected).map((episode) => episode.id),
                        current?.id,
                      ),
                    );
                }}
              >
                {selectableScopes.map(({ label }, index) => (
                  <option key={index} value={index}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              播放方式{" "}
              <select
                value={mode}
                onChange={(event) => changeMode(event.target.value as typeof mode)}
              >
                <option value="ordered">顺序</option>
                <option value="shuffle">随机</option>
                <option value="repeat">单集循环</option>
              </select>
            </label>
            <button
              className="ial-button"
              disabled={!eligible.length}
              onClick={() => {
                const episode =
                  mode === "shuffle"
                    ? eligible[Math.floor(Math.random() * eligible.length)]
                    : eligible[0];
                if (episode) play(episode, eligible, scope);
              }}
            >
              播放当前范围
            </button>
          </div>
          {!eligible.length && <QueryState empty="当前范围暂无可播放节目。" />}
          <details>
            <summary>下载与设备副本</summary>
            <div className="ial-actions">
              <button
                className="ial-button"
                disabled={action.busy || !eligible.length}
                onClick={() =>
                  void mutate(() => downloadPlaylist(eligible, "通勤播客"), "已发起 MP3 下载。")
                }
              >
                导出当前范围 MP3
              </button>
              <button
                className="ial-button"
                disabled={action.busy || !eligible.length}
                onClick={() =>
                  void mutate(() => saveOffline(eligible, "通勤播客", scope), "已保存设备副本。")
                }
              >
                保存音频到此设备
              </button>
              <button
                className="ial-button"
                disabled={action.busy}
                onClick={() => {
                  audio.pause();
                  setShowPlayer(false);
                  setDevice(true);
                }}
              >
                查看设备副本
              </button>
            </div>
          </details>
          {notice && <p role="status">{notice}</p>}
          <QueryState
            loading={audio.loading}
            error={audio.error || action.error || progressError}
          />
        </section>
      </PodcastDialog>
    </div>
  );
}
