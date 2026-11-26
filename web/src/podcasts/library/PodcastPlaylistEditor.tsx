import { useRef, useState } from "react";
import type { PodcastEpisode, PodcastLibrary, PodcastPlaylist } from "../../api/podcasts";
import {
  episodeStatus,
  moveEpisode,
  playlistEpisodes,
  readyEpisodes,
  searchEpisodes,
} from "./model";
import { PodcastHeading, PodcastIcon } from "./PodcastUi";

export function PodcastPlaylistEditor({
  library,
  playlist,
  startingEpisodeId,
  busy,
  onBack,
  onSave,
  onDelete,
}: {
  library: PodcastLibrary;
  playlist?: PodcastPlaylist;
  startingEpisodeId?: number;
  busy: boolean;
  onBack: () => void;
  onSave: (name: string, ids: number[]) => void;
  onDelete: () => void;
}) {
  const [initialIds] = useState(() => new Set(playlist?.episode_ids ?? []));
  const [name, setName] = useState(playlist?.name ?? "");
  const [ids, setIds] = useState(
    playlist?.episode_ids ?? (startingEpisodeId === undefined ? [] : [startingEpisodeId]),
  );
  const [removed, setRemoved] = useState<number[]>([]);
  const [query, setQuery] = useState("");
  const search = useRef<HTMLInputElement>(null);
  const arriving =
    playlist?.episode_ids.filter(
      (id) => !initialIds.has(id) && !ids.includes(id) && !removed.includes(id),
    ) ?? [];
  const ordered = playlistEpisodes(library, [...ids, ...arriving]);
  const included = new Set(ordered.map((episode) => episode.id));
  const available = searchEpisodes(
    readyEpisodes(library.episodes).filter((episode) => !included.has(episode.id)),
    query,
  );

  function remove(episode: PodcastEpisode) {
    setRemoved((value) => [...value, episode.id]);
    setIds((value) => value.filter((id) => id !== episode.id));
  }

  return (
    <>
      <PodcastHeading
        title={playlist ? "管理歌单" : "新建歌单"}
        sub={
          playlist ? "从歌单移除后，节目仍保存在节目库。" : "从节目库选择节目。播放顺序可随时调整。"
        }
        back={{ label: playlist ? "返回歌单" : "返回节目库", onClick: onBack }}
      />
      <form
        className="podlib-panel podlib-editor"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy && name.trim())
            onSave(
              name.trim(),
              ordered.map((episode) => episode.id),
            );
        }}
      >
        <fieldset disabled={busy}>
          <label className="podlib-field">
            歌单名称
            <input
              required
              maxLength={160}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如，上班路上"
            />
          </label>
          <div className="podlib-panel-head">
            <h2>播放顺序</h2>
            <button
              type="button"
              className="podlib-button podlib-tint"
              onClick={() => search.current?.focus()}
            >
              <PodcastIcon name="add" />
              添加节目
            </button>
          </div>
          {ordered.length ? (
            <ol className="podlib-edit-order">
              {ordered.map((episode, index) => (
                <li key={episode.id}>
                  <span>
                    {episode.title}
                    {episode.status !== "ready" && <small>{episodeStatus[episode.status]}</small>}
                  </span>
                  <div className="podlib-actions">
                    <button
                      type="button"
                      className="podlib-button podlib-outline podlib-icon-button"
                      aria-label={`上移 ${episode.title}`}
                      disabled={index === 0}
                      onClick={() =>
                        setIds(
                          moveEpisode(
                            ordered.map((item) => item.id),
                            episode.id,
                            -1,
                          ),
                        )
                      }
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className="podlib-button podlib-outline podlib-icon-button"
                      aria-label={`下移 ${episode.title}`}
                      disabled={index === ordered.length - 1}
                      onClick={() =>
                        setIds(
                          moveEpisode(
                            ordered.map((item) => item.id),
                            episode.id,
                            1,
                          ),
                        )
                      }
                    >
                      ↓
                    </button>
                    <button
                      type="button"
                      className="podlib-button podlib-outline"
                      aria-label={`从歌单移除 ${episode.title}`}
                      onClick={() => remove(episode)}
                    >
                      <PodcastIcon name="close" />
                      移除
                    </button>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p className="podlib-note">歌单还没有节目。</p>
          )}
          <label className="podlib-search">
            <PodcastIcon name="search" />
            <input
              ref={search}
              type="search"
              aria-label="搜索可添加的节目"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索可添加的节目"
            />
          </label>
          <ul className="podlib-choices podlib-add-list" aria-label="可添加的节目">
            {available.map((episode) => (
              <li key={episode.id}>
                <button
                  type="button"
                  className="podlib-choice"
                  onClick={() => setIds((value) => [...value, episode.id])}
                  aria-label={`添加 ${episode.title}`}
                >
                  <PodcastIcon name="add" />
                  <strong>{episode.title}</strong>
                </button>
              </li>
            ))}
          </ul>
          {!available.length && <p className="podlib-note">没有可添加的节目。</p>}
          <div className="podlib-actions podlib-editor-footer">
            <button type="submit" className="podlib-button podlib-primary" disabled={!name.trim()}>
              {busy ? "正在保存…" : playlist ? "保存歌单" : "创建歌单"}
            </button>
            <button type="button" className="podlib-button podlib-outline" onClick={onBack}>
              取消
            </button>
            {playlist && (
              <button
                type="button"
                className="podlib-button podlib-outline podlib-danger"
                onClick={onDelete}
              >
                <PodcastIcon name="delete" />
                删除歌单
              </button>
            )}
          </div>
        </fieldset>
      </form>
    </>
  );
}
