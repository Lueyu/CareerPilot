import type { PodcastEpisode } from "../../api/podcasts";
import { duration, episodeLanguageLabel, episodeStatus, familiarityLabel } from "./model";
import { PodcastIcon } from "./PodcastUi";

export function EpisodeList({
  episodes,
  onPlay,
  menu,
}: {
  episodes: PodcastEpisode[];
  onPlay: (episode: PodcastEpisode) => void;
  menu?: {
    openId: number | undefined;
    onToggle: (id: number) => void;
    onAdd: (id: number) => void;
    onDelete: (id: number) => void;
  };
}) {
  return (
    <ol className="podlib-episodes">
      {episodes.map((episode, index) => (
        <li key={episode.id} className="podlib-episode">
          <button
            type="button"
            className="podlib-episode-main"
            disabled={episode.status !== "ready"}
            onClick={() => onPlay(episode)}
          >
            <span className="podlib-episode-index" aria-hidden="true">
              {String(index + 1).padStart(2, "0")}
            </span>
            <span className="podlib-episode-title">
              <strong>{episode.title}</strong>
              <small>
                <span className="podlib-mobile-duration">
                  {duration(episode.duration_seconds)} ·{" "}
                </span>
                {[
                  episode.question_code,
                  episode.topic,
                  episodeLanguageLabel(episode),
                  episode.part > 1 ? `第 ${episode.part} 集` : "",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </small>
            </span>
            <span className="podlib-familiarity">
              {episode.status === "ready"
                ? familiarityLabel[episode.familiarity]
                : episodeStatus[episode.status]}
            </span>
            <span className="podlib-episode-duration">
              {episode.status === "ready" ? duration(episode.duration_seconds) : "—"}
            </span>
            <PodcastIcon name="chevron" />
          </button>
          {menu && (
            <div
              className="podlib-more"
              onKeyDown={(event) => {
                if (event.key === "Escape" && menu.openId === episode.id) {
                  event.currentTarget.querySelector("button")?.focus();
                  menu.onToggle(episode.id);
                }
              }}
            >
              <button
                type="button"
                className="podlib-button podlib-neutral podlib-icon-button"
                aria-label={`${episode.title} 的更多操作`}
                aria-expanded={episode.id === menu.openId}
                onClick={() => menu.onToggle(episode.id)}
              >
                <PodcastIcon name="more" />
              </button>
              {menu.openId === episode.id && (
                <div className="podlib-more-panel">
                  <button
                    type="button"
                    className="podlib-button podlib-neutral"
                    onClick={() => menu.onAdd(episode.id)}
                  >
                    加入歌单
                  </button>
                  <button
                    type="button"
                    className="podlib-button podlib-neutral podlib-danger"
                    onClick={() => menu.onDelete(episode.id)}
                  >
                    从节目库删除…
                  </button>
                </div>
              )}
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}
