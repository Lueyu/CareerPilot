import { useEffect, useRef, useState } from "react";
import { podcastApi, type PodcastPlaylist, type PodcastSource } from "../../api/podcasts";
import { errorDetail } from "./model";
import { PodcastHeading, PodcastIcon, PodcastSteps } from "./PodcastUi";

const PAGE_SIZE = 15;

export function PodcastImport({
  initialIds,
  playlists,
  onClose,
  onImported,
}: {
  initialIds: number[];
  playlists: PodcastPlaylist[];
  onClose: () => void;
  onImported: (ids: number[], questionIds: number[], skippedCount?: number) => void;
}) {
  const [selected, setSelected] = useState(() => new Set(initialIds));
  const [series, setSeries] = useState(false);
  const [playlistId, setPlaylistId] = useState<number>();
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [catalog, setCatalog] = useState<{ items: PodcastSource[]; total: number }>();
  const [known, setKnown] = useState(() => new Map<number, PodcastSource>());
  const [sourceError, setSourceError] = useState("");
  const [importError, setImportError] = useState("");
  const [busy, setBusy] = useState(false);
  const importing = useRef(false);

  useEffect(() => {
    let current = true;
    const timer = window.setTimeout(
      async () => {
        try {
          const result = await podcastApi.sources(query.trim(), offset, PAGE_SIZE);
          if (!current) return;
          setCatalog(result);
          setKnown(
            (previous) =>
              new Map([...previous, ...result.items.map((source) => [source.id, source] as const)]),
          );
        } catch (error) {
          if (current) setSourceError(errorDetail(error, "题目加载失败"));
        }
      },
      query ? 220 : 0,
    );
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [query, offset, attempt]);

  function select(id: number) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  }

  function loadPage(nextOffset: number) {
    setCatalog(undefined);
    setSourceError("");
    setOffset(nextOffset);
    setAttempt((value) => value + 1);
  }

  async function submit() {
    if (!selected.size || selected.size > 100 || importing.current) return;
    importing.current = true;
    setBusy(true);
    setImportError("");
    const questionIds = [...selected];
    try {
      const { episode_ids, skipped_question_ids } = await podcastApi.importQuestions(
        questionIds,
        series,
        playlistId,
      );
      if (!episode_ids.length) {
        setImportError("没有找到可导入的题目，请确认所选题目有参考答案。");
        return;
      }
      onImported(episode_ids, questionIds, skipped_question_ids?.length);
    } catch (error) {
      setImportError(errorDetail(error, "导入失败"));
    } finally {
      importing.current = false;
      setBusy(false);
    }
  }

  return (
    <>
      <PodcastHeading
        title="从题库导入"
        sub="每道题单独改写成口播，生成后可以先读讲稿。"
        back={{
          label: "返回节目库",
          onClick: () => {
            if (!importing.current) onClose();
          },
        }}
      />
      <PodcastSteps step={1} />
      <form
        className="podlib-panel"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <fieldset className="podlib-source-form" disabled={busy}>
          <legend>选择面试题</legend>
          <label className="podlib-search">
            <PodcastIcon name="search" />
            <input
              type="search"
              aria-label="搜索面试题"
              maxLength={400}
              placeholder="搜索题目、题号或主题"
              value={query}
              onChange={(event) => {
                if (event.target.value === query) return;
                setCatalog(undefined);
                setSourceError("");
                setOffset(0);
                setQuery(event.target.value);
              }}
            />
          </label>
          {selected.size > 0 && (
            <div className="podlib-selection" aria-label="已选题目">
              <strong>已选 {selected.size} 题</strong>
              <ul>
                {[...selected].map((id) => {
                  const title = known.get(id)?.title ?? `题库题目 #${id}`;
                  return (
                    <li key={id}>
                      <button
                        type="button"
                        className="podlib-button podlib-tint"
                        onClick={() => select(id)}
                        aria-label={`取消选择 ${title}`}
                      >
                        {title}
                        <PodcastIcon name="close" />
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          {sourceError ? (
            <div className="podlib-actions">
              <p role="alert">{sourceError}</p>
              <button
                className="podlib-button podlib-outline"
                type="button"
                onClick={() => loadPage(offset)}
              >
                重新加载题目
              </button>
            </div>
          ) : !catalog ? (
            <p role="status">正在加载题目…</p>
          ) : catalog.items.length === 0 ? (
            <p>没有找到符合条件的题目。</p>
          ) : (
            <ul className="podlib-choices">
              {catalog.items.map((source) => (
                <li key={source.id}>
                  <label className="podlib-choice">
                    <input
                      type="checkbox"
                      checked={selected.has(source.id)}
                      onChange={() => select(source.id)}
                    />
                    <span>
                      <strong>{source.title}</strong>
                      <small>
                        {[source.question_code, source.topic].filter(Boolean).join(" · ")}
                      </small>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          {(offset > 0 || (catalog && catalog.total > PAGE_SIZE)) && (
            <div className="podlib-actions podlib-pagination">
              <button
                type="button"
                className="podlib-button podlib-outline"
                disabled={offset === 0}
                onClick={() => loadPage(Math.max(0, offset - PAGE_SIZE))}
              >
                上一页
              </button>
              <span>
                第 {Math.floor(offset / PAGE_SIZE) + 1} 页{catalog && ` · 共 ${catalog.total} 题`}
              </span>
              <button
                type="button"
                className="podlib-button podlib-outline"
                disabled={!catalog || offset + PAGE_SIZE >= catalog.total}
                onClick={() => loadPage(offset + PAGE_SIZE)}
              >
                下一页
              </button>
            </div>
          )}
          <div className="podlib-options">
            <label className="podlib-field">
              存入歌单
              <select
                value={playlistId ?? ""}
                onChange={(event) => setPlaylistId(Number(event.target.value) || undefined)}
              >
                <option value="">仅节目库</option>
                {playlists.map((playlist) => (
                  <option key={playlist.id} value={playlist.id}>
                    {playlist.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="podlib-check">
              <input
                type="checkbox"
                checked={series}
                onChange={(event) => setSeries(event.target.checked)}
              />
              拆成系列
            </label>
          </div>
          <p className="podlib-note">题目需要有参考答案。可复用同一答案版本生成过的节目。</p>
          {selected.size > 100 && (
            <p className="podlib-error" role="alert">
              一次最多导入 100 道题，请减少选择。
            </p>
          )}
          <button
            type="submit"
            className="podlib-button podlib-primary"
            disabled={!selected.size || selected.size > 100 || busy}
          >
            <PodcastIcon name="document" />
            {busy ? "正在提交…" : selected.size ? `生成 ${selected.size} 题讲稿` : "生成讲稿"}
          </button>
        </fieldset>
        {importError && (
          <p className="podlib-error" role="alert">
            {importError}
          </p>
        )}
      </form>
    </>
  );
}
