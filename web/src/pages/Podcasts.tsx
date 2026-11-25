import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getWorkspace } from "../api/client";
import {
  podcastApi,
  type PodcastEpisode,
  type PodcastLibrary,
  type PodcastPlaylist,
} from "../api/podcasts";
import { usePodcastPlayer } from "../podcasts/PodcastPlayer";
import {
  downloadPlaylist,
  offlinePodcastUrl,
  podcastLibraryChanged,
  saveOffline,
} from "../podcasts/podcastOffline";
import { EpisodeList } from "../podcasts/library/EpisodeList";
import { PodcastLibraryView } from "../podcasts/library/PodcastLibraryView";
import { PodcastImport } from "../podcasts/library/PodcastImport";
import { PodcastPlaylistEditor } from "../podcasts/library/PodcastPlaylistEditor";
import { PodcastProduction } from "../podcasts/library/PodcastProduction";
import {
  PodcastCover,
  PodcastDialog,
  PodcastHeading,
  PodcastIcon,
} from "../podcasts/library/PodcastUi";
import {
  errorDetail,
  importedEpisodes,
  isProducing,
  parseQuestionIds,
  playlistEpisodes,
  readyEpisodes,
} from "../podcasts/library/model";
import "./podcasts.css";

type ImportBatch = {
  ids: number[];
  questionIds: number[];
  skippedCount?: number;
};

function notifyPlayer() {
  window.dispatchEvent(new CustomEvent(podcastLibraryChanged, { detail: { source: "library" } }));
}

type View =
  | { kind: "library" }
  | { kind: "import" }
  | { kind: "production"; batch?: ImportBatch }
  | { kind: "playlist" | "edit"; id: number }
  | { kind: "new"; episodeId?: number }
  | { kind: "topic"; topic: string };

type Dialog =
  | { kind: "add" | "deleteEpisode" | "deletePlaylist"; id: number }
  | { kind: "offline"; id: number };

const emptyLibrary: PodcastLibrary = { episodes: [], playlists: [] };

export default function Podcasts() {
  const workspace = getWorkspace();
  return <WorkspacePodcasts key={workspace} />;
}

function WorkspacePodcasts() {
  const player = usePodcastPlayer();
  const [params, setParams] = useSearchParams();
  const linked = params.get("questions");
  const [view, setView] = useState<View>({ kind: "library" });
  const [dialog, setDialog] = useState<Dialog>();
  const [library, setLibrary] = useState<PodcastLibrary>();
  const [fetchError, setFetchError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [savedOffline, setSavedOffline] = useState(false);
  const actionInFlight = useRef(false);
  const requestId = useRef(0);
  const screen: View = linked === null ? view : { kind: "import" };
  const data = library ?? emptyLibrary;
  const batch = screen.kind === "production" ? screen.batch : undefined;
  const active =
    data.episodes.some(isProducing) ||
    Boolean(batch?.ids.some((id) => !data.episodes.some((episode) => episode.id === id)));

  const refresh = useCallback(async () => {
    const id = ++requestId.current;
    try {
      const next = await podcastApi.library();
      if (id === requestId.current) {
        setLibrary(next);
        setFetchError("");
      }
    } catch (error) {
      if (id === requestId.current) setFetchError(errorDetail(error, "节目库刷新失败"));
    }
  }, []);

  useEffect(() => {
    const onFocus = () => {
      void refresh();
    };
    const onPlayerChange = (event: Event) => {
      if (event instanceof CustomEvent && event.detail?.source === "library") return;
      void refresh();
    };
    void refresh();
    window.addEventListener("focus", onFocus);
    window.addEventListener(podcastLibraryChanged, onPlayerChange);
    return () => {
      ++requestId.current;
      window.removeEventListener("focus", onFocus);
      window.removeEventListener(podcastLibraryChanged, onPlayerChange);
    };
  }, [refresh]);

  useEffect(() => {
    if (!active) return;
    let current = true;
    let timer: number;
    const schedule = () => {
      timer = window.setTimeout(async () => {
        await refresh();
        if (current) schedule();
      }, 2200);
    };
    schedule();
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [active, refresh]);

  function go(next: View) {
    setView(next);
    setDialog(undefined);
    setActionError("");
    setNotice("");
    if (linked !== null) {
      const nextParams = new URLSearchParams(params);
      nextParams.delete("questions");
      setParams(nextParams, { replace: true });
    }
  }

  async function act(fallback: string, job: () => Promise<void>, updateLibrary = true) {
    if (actionInFlight.current) return;
    actionInFlight.current = true;
    setBusy(true);
    setActionError("");
    setNotice("");
    try {
      await job();
    } catch (error) {
      setActionError(errorDetail(error, fallback));
    } finally {
      if (updateLibrary) await refresh();
      actionInFlight.current = false;
      setBusy(false);
    }
  }

  const backToLibrary = () => {
    go({ kind: "library" });
    void refresh();
  };
  const allReady = readyEpisodes(data.episodes);
  const edited =
    screen.kind === "edit" || screen.kind === "playlist"
      ? data.playlists.find((item) => item.id === screen.id)
      : undefined;
  const playable = edited && readyEpisodes(playlistEpisodes(data, edited.episode_ids));
  const dialogPlaylist = dialog && data.playlists.find((playlist) => playlist.id === dialog.id);
  const dialogEpisode = dialog && data.episodes.find((episode) => episode.id === dialog.id);
  const dialogPlaylistReady =
    dialogPlaylist && readyEpisodes(playlistEpisodes(data, dialogPlaylist.episode_ids));
  const offlineComplete = dialogPlaylistReady?.length === dialogPlaylist?.episode_ids.length;

  function changePlaylist(name: string, ids: number[]) {
    void act("歌单保存失败", async () => {
      const result = edited
        ? await podcastApi.updatePlaylist(edited.id, { name, episode_ids: ids })
        : await podcastApi.createPlaylist(name, ids);
      setLibrary((current) => {
        const base = current ?? emptyLibrary;
        return {
          ...base,
          playlists: base.playlists.some((item) => item.id === result.id)
            ? base.playlists.map((item) => (item.id === result.id ? result : item))
            : [...base.playlists, result],
        };
      });
      notifyPlayer();
      go({ kind: "playlist", id: result.id });
      setNotice("歌单已保存。");
    });
  }

  function openDialog(next: Dialog) {
    setActionError("");
    setNotice("");
    setSavedOffline(false);
    setDialog(next);
  }

  function addToPlaylist(playlist: PodcastPlaylist, id: number) {
    void act("加入歌单失败", async () => {
      await podcastApi.updatePlaylist(playlist.id, {
        episode_ids: [...new Set([...playlist.episode_ids, id])],
      });
      notifyPlayer();
      setDialog(undefined);
      setNotice(`已加入「${playlist.name}」。`);
    });
  }

  function deleteItem() {
    if (!dialog) return;
    if (dialog.kind === "deleteEpisode") {
      void act("节目删除失败", async () => {
        await podcastApi.remove(dialog.id);
        if (batch) {
          const ids = batch.ids.filter((id) => id !== dialog.id);
          const remaining = jobs.filter((job) => job.id !== dialog.id);
          const questionIds = batch.questionIds.filter((id) =>
            id !== dialogEpisode?.question_id || remaining.some((job) => job.question_id === id),
          );
          setView(ids.length || remaining.length
            ? { kind: "production", batch: { ...batch, ids, questionIds } }
            : { kind: "library" });
        }
        notifyPlayer();
        setDialog(undefined);
        setNotice("节目已从节目库及所有歌单删除。");
      });
    }
    if (dialog.kind === "deletePlaylist") {
      void act("歌单删除失败", async () => {
        await podcastApi.removePlaylist(dialog.id);
        notifyPlayer();
        go({ kind: "library" });
        setNotice("歌单已删除。");
      });
    }
  }

  function exportPlaylist(playlist: PodcastPlaylist, locally: boolean) {
    const episodes = readyEpisodes(playlistEpisodes(data, playlist.episode_ids));
    if (!episodes.length || (locally && episodes.length !== playlist.episode_ids.length)) return;
    void act(
      locally ? "离线保存失败" : "导出失败",
      async () => {
        if (locally) {
          await saveOffline(episodes, playlist.name, { kind: "playlist", id: playlist.id });
          setSavedOffline(true);
          setNotice("歌单已存到这台设备。");
        } else {
          await downloadPlaylist(episodes, playlist.name);
          setNotice("已请求下载歌单音频。");
        }
      },
      false,
    );
  }

  const jobs = batch
    ? importedEpisodes(data.episodes, batch)
    : data.episodes.filter((episode) => episode.status !== "ready");
  const missingQuestions =
    batch?.questionIds.filter((id) => !jobs.some((job) => job.question_id === id)).length ?? 0;

  let content;
  switch (screen.kind) {
    case "import":
      content = (
        <PodcastImport
          key={linked ?? "library"}
          initialIds={parseQuestionIds(linked)}
          playlists={data.playlists}
          onClose={backToLibrary}
          onImported={(ids, questionIds, skippedCount) => {
            go({ kind: "production", batch: { ids, questionIds, skippedCount } });
            void refresh();
            notifyPlayer();
          }}
        />
      );
      break;
    case "production":
      content = (
        <PodcastProduction
          episodes={jobs}
          waiting={Boolean(
            batch && !fetchError && batch.ids.some((id) => !jobs.some((job) => job.id === id)),
          )}
          missingQuestions={batch?.skippedCount ?? missingQuestions}
          busy={busy}
          onBack={backToLibrary}
          onPlay={player.play}
          onDelete={(id) => openDialog({ kind: "deleteEpisode", id })}
          onRetry={(id) => {
            void act("这集重试失败", async () => {
              await podcastApi.retry(id);
            });
          }}
          onSynthesize={(episodes) =>
            act("部分节目提交合成失败", async () => {
              const results = await Promise.allSettled(
                episodes.map((episode) => podcastApi.synthesize(episode.id)),
              );
              const failures = episodes.filter((_, index) => results[index].status === "rejected");
              if (failures.length)
                throw new Error(failures.map((episode) => episode.title).join("、"));
            })
          }
        />
      );
      break;
    case "new":
    case "edit":
      content =
        screen.kind === "edit" && !edited ? (
          <PodcastHeading
            title={busy ? "正在保存歌单…" : "未找到歌单"}
            back={{ label: "返回节目库", onClick: backToLibrary }}
          />
        ) : (
          <PodcastPlaylistEditor
            key={edited?.id ?? "new"}
            library={data}
            playlist={edited}
            busy={busy}
            startingEpisodeId={screen.kind === "new" ? screen.episodeId : undefined}
            onBack={() => go(edited ? { kind: "playlist", id: edited.id } : { kind: "library" })}
            onSave={changePlaylist}
            onDelete={() => {
              if (edited) openDialog({ kind: "deletePlaylist", id: edited.id });
            }}
          />
        );
      break;
    case "playlist":
      content =
        edited && playable ? (
          <>
            <PodcastHeading
              title={edited.name}
              sub={`我的歌单 · ${playable.length} 集可播放`}
              back={{ label: "返回节目库", onClick: backToLibrary }}
            >
              <button
                type="button"
                className="podlib-button podlib-outline"
                onClick={() => go({ kind: "edit", id: edited.id })}
              >
                <PodcastIcon name="manage" />
                管理歌单
              </button>
              <button
                type="button"
                className="podlib-button podlib-primary"
                disabled={!playable.length}
                onClick={() => player.play(playable[0], playable, { kind: "playlist", id: edited.id })}
              >
                <PodcastIcon name="playWhite" />
                播放歌单
              </button>
            </PodcastHeading>
            <div className="podlib-grid">
              <section className="podlib-panel">
                <div className="podlib-panel-head podlib-divider">
                  <h2>播放顺序</h2>
                </div>
                {edited.episode_ids.length ? (
                  <EpisodeList
                    episodes={playlistEpisodes(data, edited.episode_ids)}
                    onPlay={(episode) => player.play(episode, playable, { kind: "playlist", id: edited.id })}
                  />
                ) : (
                  <p>这张歌单还没有节目。</p>
                )}
              </section>
              <aside className="podlib-panel podlib-offline-card">
                <PodcastCover />
                <h2>带走这张歌单</h2>
                <p>先在设备上保存，或导出音频传到手机。</p>
                <button
                  type="button"
                  className="podlib-button podlib-tint"
                  disabled={!playable.length}
                  onClick={() => openDialog({ kind: "offline", id: edited.id })}
                >
                  <PodcastIcon name="download" />
                  查看通勤准备
                </button>
              </aside>
            </div>
          </>
        ) : (
          <PodcastHeading
            title={busy ? "正在保存歌单…" : "未找到歌单"}
            back={{ label: "返回节目库", onClick: backToLibrary }}
          />
        );
      break;
    case "topic": {
      const queue = allReady.filter((episode) => episode.topic === screen.topic);
      content = (
        <>
          <PodcastHeading
            title={screen.topic}
            sub={`${queue.length} 集可播放`}
            back={{ label: "返回节目库", onClick: backToLibrary }}
          />
          <section className="podlib-panel">
            <EpisodeList
              episodes={queue}
              onPlay={(episode) => player.play(episode, queue, { kind: "topic", topic: screen.topic })}
            />
            {!queue.length && <p>这个主题暂无可播放的节目。</p>}
          </section>
        </>
      );
      break;
    }
    case "library":
      content = (
        <PodcastLibraryView
          library={data}
          loading={!library && !fetchError}
          onImport={() => go({ kind: "import" })}
          onProduction={() => go({ kind: "production" })}
          onNew={() => go({ kind: "new" })}
          onPlaylist={(id) => go({ kind: "playlist", id })}
          onTopic={(topic) => go({ kind: "topic", topic })}
          onOpenPlayer={player.open}
          onPlay={player.play}
          onAdd={(id) => openDialog({ kind: "add", id })}
          onDelete={(id) => openDialog({ kind: "deleteEpisode", id })}
        />
      );
      break;
  }

  return (
    <main className="podcasts">
      {fetchError && (
        <div className="podlib-feedback podlib-error">
          <p role="alert">{fetchError}</p>
          <button
            type="button"
            className="podlib-button podlib-outline"
            onClick={() => {
              void refresh();
            }}
          >
            刷新节目库
          </button>
        </div>
      )}
      {actionError && !dialog && (
        <p className="podlib-feedback podlib-error" role="alert">
          {actionError}
        </p>
      )}
      {notice && !dialog && (
        <p className="podlib-feedback" role="status">
          {notice}
        </p>
      )}
      {content}

      {dialog?.kind === "add" && dialogEpisode && (
        <PodcastDialog title="加入歌单" onClose={() => setDialog(undefined)}>
          <p>{dialogEpisode.title}</p>
          {data.playlists.length ? (
            <ul className="podlib-choices">
              {data.playlists.map((playlist) => {
                const already = playlist.episode_ids.includes(dialogEpisode.id);
                return (
                  <li key={playlist.id}>
                    <button
                      type="button"
                      className="podlib-choice"
                      disabled={busy || already}
                      onClick={() => addToPlaylist(playlist, dialogEpisode.id)}
                    >
                      {playlist.name}
                      {already && " · 已加入"}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
          <button
            type="button"
            className="podlib-button podlib-tint"
            disabled={busy}
            onClick={() => go({ kind: "new", episodeId: dialogEpisode.id })}
          >
            新建歌单
          </button>
          {actionError && (
            <p className="podlib-error" role="alert">
              {actionError}
            </p>
          )}
        </PodcastDialog>
      )}

      {(dialog?.kind === "deleteEpisode" || dialog?.kind === "deletePlaylist") && (
        <PodcastDialog
          title={dialog.kind === "deleteEpisode" ? "从节目库删除节目？" : "删除歌单？"}
          onClose={() => {
            if (!busy) setDialog(undefined);
          }}
        >
          <p>
            {dialog.kind === "deleteEpisode"
              ? `「${dialogEpisode?.title ?? ""}」及其所有歌单关联将被删除。原题、答题、评分和答案接触记录会保留。`
              : `「${dialogPlaylist?.name ?? ""}」及其节目关联将被删除。节目仍保存在节目库。`}
          </p>
          <div className="podlib-actions">
            <button
              type="button"
              className="podlib-button podlib-outline"
              disabled={busy}
              onClick={() => setDialog(undefined)}
            >
              取消
            </button>
            <button
              type="button"
              className="podlib-button podlib-danger-solid"
              disabled={busy}
              onClick={deleteItem}
            >
              确认删除
            </button>
          </div>
          {actionError && (
            <p className="podlib-error" role="alert">
              {actionError}
            </p>
          )}
        </PodcastDialog>
      )}

      {dialog?.kind === "offline" && dialogPlaylist && (
        <PodcastDialog
          title="带走这张歌单"
          onClose={() => {
            if (!busy) setDialog(undefined);
          }}
        >
          <p>将可播放的节目保存到这台设备，或按歌单顺序导出音频。</p>
          {!offlineComplete && (
            <p className="podlib-note">待所有节目合成后可保存整张歌单。导出会跳过未完成的集数。</p>
          )}
          <div className="podlib-actions">
            <button
              type="button"
              className="podlib-button podlib-primary"
              disabled={busy || !offlineComplete || !dialogPlaylistReady?.length}
              onClick={() => exportPlaylist(dialogPlaylist, true)}
            >
              保存到这台设备
            </button>
            <button
              type="button"
              className="podlib-button podlib-outline"
              disabled={busy || !dialogPlaylistReady?.length}
              onClick={() => exportPlaylist(dialogPlaylist, false)}
            >
              <PodcastIcon name="download" />
              导出歌单音频
            </button>
          </div>
          {busy && <p role="status">正在准备音频…</p>}
          {notice && <p role="status">{notice}</p>}
          {savedOffline && (
            <a className="podlib-button podlib-tint" href={offlinePodcastUrl}>
              打开离线播放器
            </a>
          )}
          {actionError && (
            <p className="podlib-error" role="alert">
              {actionError}
            </p>
          )}
        </PodcastDialog>
      )}
    </main>
  );
}
