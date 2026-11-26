import { useEffect, useRef, useState } from "react";
import { podcastApi, type PodcastEpisode } from "../../api/podcasts";
import type { StartPodcastPlayback } from "../podcastQueue";
import { canPreview, episodeLanguageLabel, episodeStatus, errorDetail, readyEpisodes } from "./model";
import { PodcastDialog, PodcastHeading, PodcastIcon, PodcastSteps } from "./PodcastUi";

type PreviewState =
  | { kind: "loading" }
  | { kind: "script"; title: string; script: string }
  | { kind: "error"; message: string };

export function PodcastScript({ episode }: { episode: PodcastEpisode }) {
  const [attempt, setAttempt] = useState(0);
  const [preview, setPreview] = useState<PreviewState>({ kind: "loading" });

  useEffect(() => {
    let current = true;
    podcastApi.script(episode.id).then(
      (result) => {
        if (current) setPreview({ kind: "script", ...result });
      },
      (error) => {
        if (current) setPreview({ kind: "error", message: errorDetail(error, "讲稿加载失败") });
      },
    );
    return () => {
      current = false;
    };
  }, [episode.id, attempt]);

  if (preview.kind === "loading") return <p role="status">正在打开讲稿…</p>;
  if (preview.kind === "error")
    return (
      <div className="podlib-actions">
        <p role="alert">{preview.message}</p>
        <button
          type="button"
          className="podlib-button podlib-outline"
          onClick={() => {
            setPreview({ kind: "loading" });
            setAttempt((value) => value + 1);
          }}
        >
          重新打开讲稿
        </button>
      </div>
    );
  return (
    <article className="podlib-script">
      <p className="podlib-note">第 {episode.part} 集 · {episodeLanguageLabel(episode)}</p>
      <h2>{preview.title}</h2>
      <div className="podlib-transcript">{preview.script}</div>
    </article>
  );
}

export function PodcastProduction({
  episodes,
  waiting,
  missingQuestions,
  busy,
  onBack,
  onSynthesize,
  onRetry,
  onDelete,
  onPlay,
}: {
  episodes: PodcastEpisode[];
  waiting: boolean;
  missingQuestions: number;
  busy: boolean;
  onBack: () => void;
  onSynthesize: (episodes: PodcastEpisode[]) => Promise<void>;
  onRetry: (id: number) => void;
  onDelete: (id: number) => void;
  onPlay: StartPodcastPlayback;
}) {
  const [previewId, setPreviewId] = useState<number>();
  const [selected, setSelected] = useState<number[]>([]);
  const [confirmedIds, setConfirmedIds] = useState<number[]>();
  const previewPanel = useRef<HTMLElement>(null);
  const previewOpener = useRef<HTMLButtonElement>();
  const ready = readyEpisodes(episodes);
  const confirmed = episodes.filter(
    (episode) => episode.status === "draft" && confirmedIds?.includes(episode.id),
  );
  const draftSelection = episodes.filter(
    (episode) => episode.status === "draft" && selected.includes(episode.id),
  );
  const preview = episodes.find((episode) => episode.id === previewId && canPreview(episode));
  const step =
    episodes.some(
      (episode) =>
        episode.status === "draft" ||
        episode.status === "generating" ||
        episode.status === "script_failed",
    ) || waiting
      ? 2
      : 3;
  const showPreview = step === 2 || Boolean(preview);

  useEffect(() => {
    if (previewId && window.matchMedia("(max-width: 900px)").matches) {
      previewPanel.current?.scrollIntoView({ block: "start" });
      previewPanel.current?.focus({ preventScroll: true });
    }
  }, [previewId]);

  return (
    <>
      <PodcastHeading
        title={step === 2 ? "预览讲稿" : "导入结果"}
        sub={
          step === 2
            ? "逐集检查讲稿，选中要合成的集数。"
            : "已完成的节目可以先听，音频合成失败的节目保留讲稿。"
        }
        back={{ label: "返回节目库", onClick: onBack }}
      >
        {draftSelection.length > 0 && (
          <button
            type="button"
            className="podlib-button podlib-primary"
            disabled={busy}
            onClick={() => setConfirmedIds(draftSelection.map((episode) => episode.id))}
          >
            <PodcastIcon name="headphones" />
            确认并合成 {draftSelection.length} 集
          </button>
        )}
      </PodcastHeading>
      <PodcastSteps step={step} />
      {missingQuestions > 0 && !waiting && (
        <p className="podlib-error" role="alert">
          有 {missingQuestions} 道所选题目没有返回制作记录，请检查这些题目是否有参考答案，再试一次。
        </p>
      )}
      <div className={showPreview ? "podlib-grid podlib-review-grid" : undefined}>
        <section className="podlib-panel" aria-label="制作进度">
          <div className="podlib-panel-head">
            <h2>制作进度</h2>
            <span className="podlib-note" role="status" aria-live="polite">
              已入库 {ready.length} / {episodes.length} 集
            </span>
          </div>
          {waiting && <p role="status">正在加载制作记录…</p>}
          {!waiting && !episodes.length && <p>当前没有待处理的节目。</p>}
          <ul className="podlib-jobs">
            {episodes.map((episode) => (
              <li
                key={episode.id}
                className={preview?.id === episode.id ? "podlib-job-current" : undefined}
              >
                <div>
                  <strong>{episode.title}</strong>
                  <small>
                    {[episode.question_code, `第 ${episode.part} 集`, episodeLanguageLabel(episode)]
                      .filter(Boolean)
                      .join(" · ")}
                  </small>
                </div>
                <span className="podlib-stage" data-status={episode.status}>
                  {episodeStatus[episode.status]}
                </span>
                {episode.error && <small className="podlib-error">{episode.error}</small>}
                <div className="podlib-actions podlib-job-actions">
                  {canPreview(episode) && (
                    <button
                      type="button"
                      className="podlib-button podlib-outline"
                      aria-pressed={preview?.id === episode.id}
                      onClick={(event) => {
                        previewOpener.current = event.currentTarget;
                        setPreviewId(episode.id);
                      }}
                    >
                      {preview?.id === episode.id ? "正在预览" : "预览讲稿"}
                    </button>
                  )}
                  {episode.status === "draft" && (
                    <label className="podlib-check">
                      <input
                        type="checkbox"
                        checked={selected.includes(episode.id)}
                        disabled={busy}
                        aria-label={`合成 ${episode.title} 第 ${episode.part} 集`}
                        onChange={(event) => {
                          const checked = event.target.checked;
                          setSelected((ids) =>
                            checked ? [...ids, episode.id] : ids.filter((id) => id !== episode.id),
                          );
                        }}
                      />
                      合成此集
                    </label>
                  )}
                  {(episode.status === "script_failed" || episode.status === "audio_failed") && (
                    <button
                      type="button"
                      className="podlib-button podlib-tint"
                      disabled={busy}
                      onClick={() => onRetry(episode.id)}
                    >
                      <PodcastIcon name="retry" />
                      {episode.status === "script_failed" ? "重试这集讲稿" : "只重试这一集"}
                    </button>
                  )}
                  {episode.status !== "ready" && (
                    <button type="button" className="podlib-button podlib-outline" disabled={busy}
                      aria-label={`删除节目，${episode.title}`} onClick={() => onDelete(episode.id)}>
                      删除
                    </button>
                  )}
                  {episode.status === "ready" && (
                    <button
                      type="button"
                      className="podlib-button podlib-tint"
                      onClick={() => onPlay(episode, ready, {
                        kind: "selection",
                        source: "production",
                        episodeIds: ready.map((item) => item.id),
                      })}
                    >
                      <PodcastIcon name="play" />
                      播放
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
        {showPreview && (
          <section
            ref={previewPanel}
            tabIndex={-1}
            className="podlib-panel podlib-preview"
            aria-label="讲稿预览"
          >
            {preview && (
              <button
                type="button"
                className="podlib-button podlib-outline podlib-preview-back"
                onClick={() => {
                  previewOpener.current?.focus();
                  setPreviewId(undefined);
                }}
              >
                <PodcastIcon name="back" />
                返回制作进度
              </button>
            )}
            {preview ? (
              <PodcastScript key={preview.id} episode={preview} />
            ) : (
              <>
                <h2>讲稿预览</h2>
                <p className="podlib-note">打开讲稿会记录为已接触该题的答案。</p>
              </>
            )}
          </section>
        )}
      </div>
      {confirmedIds && (
        <PodcastDialogForSynthesis
          count={confirmed.length}
          busy={busy}
          onClose={() => setConfirmedIds(undefined)}
          onConfirm={() => {
            setConfirmedIds(undefined);
            void onSynthesize(confirmed);
          }}
        />
      )}
    </>
  );
}

function PodcastDialogForSynthesis({
  count,
  busy,
  onClose,
  onConfirm,
}: {
  count: number;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <PodcastDialog title="确认合成音频" onClose={onClose}>
      <p>合成选中的 {count} 集。其余讲稿会留在制作记录中。</p>
      <div className="podlib-actions">
        <button type="button" className="podlib-button podlib-outline" onClick={onClose}>
          取消
        </button>
        <button
          type="button"
          className="podlib-button podlib-primary"
          onClick={onConfirm}
          disabled={busy || !count}
        >
          合成选中的节目
        </button>
      </div>
    </PodcastDialog>
  );
}
