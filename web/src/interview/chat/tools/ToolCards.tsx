import { useEffect, useRef, useState } from "react";
import {
  Check,
  FileText,
  Loader2,
  RotateCcw,
  Search,
  SquareTerminal,
  TriangleAlert,
} from "lucide-react";
import { parsedResume, sandboxCase, searchResults } from "./model";
import {
  ToolCard,
  chipClass,
  ghostBtnClass,
  hintClass,
  outlineBtnClass,
  primaryBtnClass,
  sectionLabelClass,
} from "./ui";

// ===== 联网搜索 =====

export function WebSearchCard({ keyword }: { keyword: string }) {
  const { total, items } = searchResults(keyword);
  const [saved, setSaved] = useState<number[]>([]);
  const [visible, setVisible] = useState(3);
  const shown = items.slice(0, visible);
  return (
    <ToolCard icon={<Search size={15} />} title="联网搜索" detail={`${total} 条结果`}>
      <p className={hintClass}>关键词：{keyword || "字节跳动 后端 面经"} · 已用时 1.8s</p>
      <ul className="flex flex-col gap-2">
        {shown.map((item, index) => {
          const isSaved = saved.includes(index);
          return (
            <li
              key={item.title}
              className="flex items-start gap-3 rounded-lg bg-[var(--ia-bg)] px-3 py-2.5"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] font-medium text-[var(--blue)]">{item.title}</p>
                <p className="mt-1 text-xs leading-relaxed text-[var(--ia-muted)]">{item.summary}</p>
                <p className="mt-1 text-[11px] text-[var(--ia-muted)]">
                  {item.site} · {item.time}
                </p>
              </div>
              <button
                type="button"
                className={
                  isSaved
                    ? chipClass + " bg-[var(--ia-mint)] text-[var(--ia-green)]"
                    : outlineBtnClass
                }
                disabled={isSaved}
                onClick={() => setSaved((current) => [...current, index])}
              >
                {isSaved && <Check size={12} />}
                {isSaved ? "已保存" : "保存到面经库"}
              </button>
            </li>
          );
        })}
      </ul>
      <div className="flex items-center justify-center gap-2">
        <span className={hintClass}>
          已显示 {shown.length} / {total} 条
        </span>
        <button
          type="button"
          className={ghostBtnClass + " text-xs"}
          disabled={visible >= total}
          onClick={() => setVisible((current) => Math.min(current + 2, total))}
        >
          {visible >= total ? "已全部加载" : "加载更多"}
        </button>
      </div>
    </ToolCard>
  );
}

// ===== 文件解析 =====

type ParseState = "parsing" | "done" | "failed";

export function FileParseCard() {
  const [state, setState] = useState<ParseState>("parsing");
  const [progress, setProgress] = useState(8);
  const [saved, setSaved] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => {
    if (state !== "parsing") return;
    timer.current = setInterval(() => {
      setProgress((current) => {
        if (current >= 100) {
          clearInterval(timer.current);
          setState("done");
          return 100;
        }
        return Math.min(current + 6 + Math.round(Math.random() * 8), 100);
      });
    }, 220);
    return () => clearInterval(timer.current);
  }, [state]);

  const badge =
    state === "parsing"
      ? chipClass + " bg-[var(--ia-sand)] text-[var(--ia-amber)]"
      : state === "done"
        ? chipClass + " bg-[var(--ia-mint)] text-[var(--ia-green)]"
        : chipClass + " bg-[var(--red-bg)] text-[var(--red)]";
  const badgeText =
    state === "parsing" ? `解析中 ${progress}%` : state === "done" ? "解析完成" : "解析失败";

  return (
    <ToolCard
      icon={<FileText size={15} />}
      title={parsedResume.fileName}
      detail={
        <span className={`inline-flex items-center gap-1 ${badge}`}>
          {state === "parsing" && <Loader2 size={11} className="animate-spin" />}
          {badgeText}
        </span>
      }
    >
      {state === "parsing" && (
        <div className="flex flex-col gap-1.5">
          <div className="h-1.5 overflow-hidden rounded-full bg-[var(--ia-line)]">
            <div
              className="ia-tool-parsing-track h-full rounded-full transition-[width] duration-200"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className={hintClass}>
            正在解析第 {Math.max(1, Math.ceil(progress / 20))} / 5 页 · 已识别 2 个项目经历
          </p>
        </div>
      )}
      {state === "done" && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-2">
            <p className={sectionLabelClass}>提取的项目</p>
            {parsedResume.projects.map((project) => (
              <div key={project.name} className="rounded-lg bg-[var(--ia-bg)] px-3 py-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-[13px] font-medium text-[var(--ia-ink)]">{project.name}</p>
                  {project.stack.map((item) => (
                    <span
                      key={item}
                      className={chipClass + " bg-[var(--ia-tint)] text-[var(--ia-accent)]"}
                    >
                      {item}
                    </span>
                  ))}
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-[var(--ia-muted)]">
                  {project.description}
                </p>
              </div>
            ))}
          </div>
          <div className="flex flex-col gap-1.5">
            <p className={sectionLabelClass}>技能标签</p>
            <div className="flex flex-wrap gap-1.5">
              {parsedResume.skills.map((skill) => (
                <span key={skill} className={chipClass + " bg-[var(--ia-bg)] text-[var(--ia-muted)]"}>
                  {skill}
                </span>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className={saved ? ghostBtnClass : primaryBtnClass}
              disabled={saved}
              onClick={() => setSaved(true)}
            >
              {saved ? "已保存到项目背景库" : "确认保存到项目背景库"}
            </button>
            <button type="button" className={ghostBtnClass}>
              编辑
            </button>
          </div>
        </div>
      )}
      {state === "failed" && (
        <div className="flex items-center gap-3">
          <p className="flex items-center gap-1.5 text-xs text-[var(--red)]">
            <TriangleAlert size={13} />
            文件内容无法识别：可能是加密 PDF 或扫描件。
          </p>
          <button
            type="button"
            className={ghostBtnClass + " text-xs"}
            onClick={() => {
              setProgress(8);
              setState("parsing");
            }}
          >
            <RotateCcw size={12} />
            重试
          </button>
        </div>
      )}
      <p className={hintClass + " flex items-center justify-between"}>
        <span>mock 演示：可触发一次失败状态来验证重试</span>
        {state === "done" && (
          <button
            type="button"
            className="text-[var(--ia-muted)] underline decoration-dotted"
            onClick={() => setState("failed")}
          >
            模拟失败
          </button>
        )}
      </p>
    </ToolCard>
  );
}

// ===== 代码执行沙箱 =====

export function CodeSandboxCard() {
  const [code, setCode] = useState(sandboxCase.starterCode);
  const [running, setRunning] = useState(false);
  const [ranAt, setRanAt] = useState<number | null>(null);
  return (
    <ToolCard
      icon={<SquareTerminal size={15} />}
      title="代码执行沙箱"
      detail={
        <span className={chipClass + " bg-[var(--ia-bg)] text-[var(--ia-muted)]"}>
          {sandboxCase.language}
        </span>
      }
    >
      <textarea
        aria-label="代码输入"
        spellCheck={false}
        value={code}
        rows={10}
        onChange={(event) => setCode(event.target.value)}
        className="w-full resize-y rounded-lg bg-[var(--navy)] p-3 font-mono text-[11.5px] leading-relaxed text-[#e7eaf0] outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--ia-accent)]"
      />
      <div className="flex items-center gap-2.5">
        <button
          type="button"
          className={primaryBtnClass}
          disabled={running}
          onClick={() => {
            setRunning(true);
            setRanAt(null);
            setTimeout(() => {
              setRunning(false);
              setRanAt(Date.now());
            }, 900);
          }}
        >
          {running ? <Loader2 size={13} className="animate-spin" /> : null}
          {running ? "运行中…" : "运行"}
        </button>
        <span className={chipClass + " bg-[var(--ia-bg)] text-[var(--ia-muted)]"}>
          {sandboxCase.tests.length} 个测试用例 · 12ms
        </span>
        {ranAt && <span className={hintClass}>运行 #{(ranAt % 90) + 10} · 刚刚</span>}
      </div>
      {ranAt && (
        <ul className="flex flex-col gap-1.5">
          {sandboxCase.tests.map((testCase, index) => (
            <li key={testCase.input} className="rounded-lg bg-[var(--ia-bg)] px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-[var(--ia-muted)]">
                  用例 {index + 1}
                </span>
                <span
                  className={
                    chipClass +
                    (testCase.pass
                      ? " ml-auto bg-[var(--ia-mint)] text-[var(--ia-green)]"
                      : " ml-auto bg-[var(--red-bg)] text-[var(--red)]")
                  }
                >
                  {testCase.pass ? "✓ 通过" : "✗ 失败"}
                </span>
              </div>
              <p className="mt-1 font-mono text-[11px] text-[var(--ia-muted)]">
                输入: {testCase.input}　期望: {testCase.expect}　实际: {testCase.actual}
              </p>
            </li>
          ))}
        </ul>
      )}
      <div className="rounded-lg bg-[var(--ia-tint)] px-3 py-2.5">
        <p className="text-xs font-semibold text-[var(--ia-accent)]">AI 解析</p>
        <p className="mt-1 text-xs leading-relaxed text-[var(--ia-muted)]">{sandboxCase.analysis}</p>
      </div>
    </ToolCard>
  );
}
