import { useState } from "react";
import { api, workspaceStorageKey, type BankData, type QuestionDetail } from "../../api/client";
import { bankSourcePresentation } from "../../lib/bankSource";
import { useBankInventory } from "./useBankInventory";
import {
  ButtonLink,
  examLink,
  Icon,
  PageTitle,
  QueryState,
  reveal,
  Reference,
  Row,
  Search,
  useAction,
  useRead,
} from "./ui";

type Question = BankData["items"][number];
const pageSize = 40;

function readFavorites(key: string): string[] {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(key) ?? "[]");
    return Array.isArray(stored)
      ? stored.filter((value): value is string => typeof value === "string")
      : [];
  } catch {
    return [];
  }
}

function QuestionEditor({
  question,
  onSaved,
  onCancel,
}: {
  question: Question;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(question.title);
  const [topic, setTopic] = useState(question.topic);
  const [difficulty, setDifficulty] = useState(question.difficulty);
  const [tags, setTags] = useState(question.tags?.join("，") ?? "");
  const action = useAction();
  const changed =
    title.trim() !== question.title ||
    topic.trim() !== question.topic ||
    difficulty !== question.difficulty;
  return (
    <form
      className="ial-form ial-inset"
      ref={reveal}
      onSubmit={(event) => {
        event.preventDefault();
        if (!title.trim() || !topic.trim()) return;
        void action.run(async () => {
          await api.patch("/bank/questions/" + encodeURIComponent(question.short_code), {
            title: title.trim(),
            topic: topic.trim(),
            difficulty,
            tags: [
              ...new Set(
                tags
                  .split(/[,，]/)
                  .map((value) => value.trim())
                  .filter(Boolean),
              ),
            ],
          });
          onSaved();
        });
      }}
    >
      <h3>编辑题目</h3>
      <label>
        题目
        <textarea
          aria-label="题目"
          required
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          rows={3}
        />
      </label>
      <label>
        知识点
        <input required value={topic} onChange={(event) => setTopic(event.target.value)} />
      </label>
      <label>
        难度
        <select value={difficulty} onChange={(event) => setDifficulty(Number(event.target.value))}>
          {[1, 2, 3, 4, 5].map((value) => (
            <option key={value} value={value}>
              {value} / 5
            </option>
          ))}
        </select>
      </label>
      <label>
        标签（用逗号分隔）
        <input value={tags} onChange={(event) => setTags(event.target.value)} />
      </label>
      {changed && <small>更改题意、知识点或难度后，评分标准需要重新审核。</small>}
      <QueryState error={action.error} />
      <div className="ial-actions">
        <button
          className="ial-button ial-primary"
          disabled={action.busy || !title.trim() || !topic.trim()}
        >
          保存
        </button>
        <button type="button" className="ial-button" disabled={action.busy} onClick={onCancel}>
          取消
        </button>
      </div>
    </form>
  );
}

function QuestionReference({ question }: { question: Question }) {
  const answer = useRead<QuestionDetail>("/bank/" + encodeURIComponent(question.short_code));
  return (
    <div className="ial-inset">
      <QueryState {...answer} retry={answer.reload} />
      {answer.data && (
        <>
          <h3>参考讲解</h3>
          {answer.data.options?.length ? (
            <ol>
              {answer.data.options.map((value, index) => (
                <li key={index}>{value}</li>
              ))}
            </ol>
          ) : null}
          {answer.data.answer_html ? (
            <Reference html={answer.data.answer_html} />
          ) : (
            <p>暂无参考讲解。</p>
          )}
        </>
      )}
    </div>
  );
}

export default function Questions() {
  const [query, setQuery] = useState("");
  const [topic, setTopic] = useState("");
  const [difficulty, setDifficulty] = useState("");
  const [sort, setSort] = useState("default");
  const [scope, setScope] = useState("all");
  const [offset, setOffset] = useState(0);
  const [table, setTable] = useState(false);
  const [selection, setSelection] = useState<number[]>();
  const [tag, setTag] = useState("");
  const [batchNotice, setBatchNotice] = useState("");
  const batch = useAction();
  const [expanded, setExpanded] = useState<{ id: number; kind: "reference" | "edit" }>();
  const [favoriteError, setFavoriteError] = useState("");
  const key = workspaceStorageKey("interview-library-favorites");
  const [favorites, setFavorites] = useState(() => readFavorites(key));
  const source = useBankInventory(
    "/bank?" +
      new URLSearchParams({
        q: query.trim(),
        topic,
        scope: scope === "mine" ? "mine" : "all",
      }),
  );
  const rows = (source.data?.items ?? []).filter(
    (question) =>
      (!difficulty || question.difficulty === Number(difficulty)) &&
      (scope !== "favorites" || favorites.includes(question.short_code)),
  );
  const selectedForBatch =
    source.data?.items.filter((question) => selection?.includes(question.id)) ?? [];
  if (sort === "difficulty") rows.sort((a, b) => b.difficulty - a.difficulty);
  if (sort === "new") rows.sort((a, b) => b.id - a.id);
  const visible = rows.slice(offset, offset + pageSize);
  const selected = visible.find((question) => question.id === expanded?.id);
  const exactCount = !source.data || source.data.items.length === source.data.total;
  const filtered = !!difficulty || scope === "favorites";

  function toggleFavorite(code: string) {
    const next = favorites.includes(code)
      ? favorites.filter((value) => value !== code)
      : [...favorites, code];
    try {
      localStorage.setItem(key, JSON.stringify(next));
      setFavorites(next);
      if (scope === "favorites") setOffset(0);
      setFavoriteError("");
    } catch {
      setFavoriteError("此浏览器无法保存收藏。");
    }
  }

  function saveBatch() {
    const newTag = tag.trim();
    if (!selectedForBatch.length || !newTag) return;
    void batch.run(async () => {
      const failed: number[] = [];
      let error = "";
      for (const question of selectedForBatch) {
        if (question.tags?.includes(newTag)) continue;
        try {
          await api.patch("/bank/questions/" + encodeURIComponent(question.short_code), {
            tags: [...(question.tags ?? []), newTag],
          });
        } catch (failure) {
          failed.push(question.id);
          if (!error) error = failure instanceof Error ? failure.message : "请求失败。";
        }
      }
      source.reload();
      setSelection(failed.length ? failed : undefined);
      setBatchNotice(`${selectedForBatch.length - failed.length} 道选中题目已包含此标签。`);
      if (failed.length) throw new Error(`${failed.length} 道题目更新未确认。 ${error}`);
      setTag("");
    });
  }
  function controls(question: Question) {
    const saved = favorites.includes(question.short_code);
    const id = question.id;
    const editable =
      scope === "mine" && !question.review_only && bankSourcePresentation(question).editable;
    return (
      <>
        {selection && editable && (
          <input
            type="checkbox"
            aria-label={"选择 " + question.title}
            checked={selection.includes(id)}
            disabled={batch.busy}
            onChange={(event) =>
              setSelection(
                event.target.checked
                  ? [...selection, id]
                  : selection.filter((value) => value !== id),
              )
            }
          />
        )}
        <button
          className="ial-button ial-icon-button"
          title={saved ? "取消收藏" : "收藏"}
          aria-label={(saved ? "取消收藏 " : "收藏 ") + question.title}
          aria-pressed={saved}
          onClick={() => toggleFavorite(question.short_code)}
        >
          <Icon name="bookmark" />
        </button>
        <button
          className="ial-button"
          aria-expanded={expanded?.id === id && expanded.kind === "reference"}
          onClick={() =>
            setExpanded(
              expanded?.id === id && expanded.kind === "reference"
                ? undefined
                : { id, kind: "reference" },
            )
          }
        >
          参考讲解
        </button>
        {editable && (
          <button className="ial-button" onClick={() => setExpanded({ id, kind: "edit" })}>
            编辑
          </button>
        )}
      </>
    );
  }

  return (
    <>
      <PageTitle
        title="我的题库"
        intro={
          source.data
            ? `${filtered ? rows.length : source.data.total} 道题${filtered && !exactCount ? "（已加载范围）" : ""}`
            : undefined
        }
      />
      <section className="ial-panel">
        <div className="ial-toolbar">
          <Search
            value={query}
            onChange={(value) => {
              setQuery(value);
              setOffset(0);
              setSelection(undefined);
            }}
            placeholder="搜索题目"
          />
          <select
            aria-label="题目范围"
            value={scope}
            onChange={(event) => {
              setScope(event.target.value);
              setOffset(0);
              setExpanded(undefined);
              setSelection(undefined);
            }}
          >
            <option value="all">全部题目</option>
            <option value="mine">工作区题目</option>
            <option value="favorites">此浏览器收藏</option>
          </select>
          <select
            aria-label="知识点"
            value={topic}
            onChange={(event) => {
              setTopic(event.target.value);
              setOffset(0);
              setSelection(undefined);
            }}
          >
            <option value="">全部知识点</option>
            {[
              ...new Set([
                ...(topic ? [topic] : []),
                ...(source.data?.facets?.topics ?? []).map((item) => item.name),
              ]),
            ].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
          <select
            aria-label="难度"
            value={difficulty}
            onChange={(event) => {
              setDifficulty(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">全部难度</option>
            {[1, 2, 3, 4, 5].map((value) => (
              <option key={value} value={value}>
                {value} / 5
              </option>
            ))}
          </select>
          <select
            aria-label="排序"
            value={sort}
            onChange={(event) => {
              setSort(event.target.value);
              setOffset(0);
            }}
          >
            <option value="default">默认排序</option>
            <option value="new">最新加入</option>
            <option value="difficulty">难度从高到低</option>
          </select>
          <span className="ial-spacer" />
          <button className="ial-button" onClick={() => setTable((value) => !value)}>
            {table ? "列表视图" : "表格视图"}
          </button>
          <ButtonLink to={examLink(topic)} primary>
            从题库抽题
          </ButtonLink>
        </div>
        <QueryState
          {...source}
          retry={source.reload}
          empty={source.data && !visible.length ? "没有符合条件的题目。" : undefined}
        />
        {favoriteError && <p role="alert">{favoriteError}</p>}
        {table && visible.length > 0 ? (
          <div className="ial-table-wrap">
            <table className="ial-table">
              <thead>
                <tr>
                  <th>题目</th>
                  <th>知识点</th>
                  <th>难度</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((question) => (
                  <tr key={question.id}>
                    <th scope="row">{question.title}</th>
                    <td>{question.topic}</td>
                    <td>{question.difficulty} / 5</td>
                    <td>
                      <div className="ial-actions">{controls(question)}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <ul className="ial-list">
            {visible.map((question) => (
              <Row
                icon="questions"
                key={question.id}
                title={question.title}
                subtitle={[
                  question.topic,
                  bankSourcePresentation({ ...question, company: question.company ?? "" }).company,
                  ...(question.tags ?? []),
                ]
                  .filter(Boolean)
                  .join(" · ")}
                status={`${question.difficulty} / 5`}
              >
                {controls(question)}
              </Row>
            ))}
          </ul>
        )}
        {selected && expanded?.kind === "reference" && (
          <section className="ial-open" ref={reveal} aria-label={selected.title}>
            <div className="ial-toolbar">
              <h3>{selected.title}</h3>
              <button className="ial-button" onClick={() => setExpanded(undefined)}>
                关闭讲解
              </button>
            </div>
            <QuestionReference key={selected.id} question={selected} />
            <div className="ial-actions">
              <ButtonLink to={examLink(selected.topic)}>练习这个知识点</ButtonLink>
              {!selected.review_only && (
                <ButtonLink to={`/library/podcasts?questions=${selected.id}`}>制作播客</ButtonLink>
              )}
            </div>
          </section>
        )}
        {selected && expanded?.kind === "edit" && (
          <QuestionEditor
            key={selected.id}
            question={selected}
            onSaved={() => {
              setExpanded(undefined);
              source.reload();
            }}
            onCancel={() => setExpanded(undefined)}
          />
        )}
        <div className="ial-footer">
          {offset > 0 && (
            <button
              className="ial-button"
              onClick={() => setOffset((value) => Math.max(0, value - pageSize))}
            >
              上一页
            </button>
          )}
          {offset + pageSize < rows.length && (
            <button className="ial-button" onClick={() => setOffset((value) => value + pageSize)}>
              下一页
            </button>
          )}
          <ButtonLink to="/library/notes?import=1">导入题目</ButtonLink>
          {(query || topic || difficulty || scope !== "all") && (
            <button
              className="ial-button"
              onClick={() => {
                setQuery("");
                setTopic("");
                setDifficulty("");
                setScope("all");
                setOffset(0);
                setExpanded(undefined);
                setSelection(undefined);
              }}
            >
              清除筛选
            </button>
          )}
        </div>
        {scope === "mine" && (
          <div className="ial-toolbar">
            {!selection ? (
              <button
                className="ial-button"
                onClick={() => {
                  setSelection([]);
                  setBatchNotice("");
                }}
              >
                批量编辑标签
              </button>
            ) : (
              <form
                className="ial-toolbar"
                onSubmit={(event) => {
                  event.preventDefault();
                  saveBatch();
                }}
              >
                <label>
                  为已选的 {selectedForBatch.length} 道题添加标签{" "}
                  <input
                    value={tag}
                    maxLength={80}
                    required
                    onChange={(event) => setTag(event.target.value)}
                  />
                </label>
                <button
                  className="ial-button ial-primary"
                  disabled={batch.busy || !tag.trim() || !selectedForBatch.length}
                >
                  保存标签
                </button>
                <button
                  className="ial-button"
                  type="button"
                  disabled={batch.busy}
                  onClick={() => setSelection(undefined)}
                >
                  结束选择
                </button>
              </form>
            )}
          </div>
        )}
        <QueryState error={batch.error} />
        {batchNotice && <p role="status">{batchNotice}</p>}
        {!exactCount && <small>当前最多加载 5,000 道题。请缩小搜索范围以查看其余题目。</small>}
      </section>
    </>
  );
}
