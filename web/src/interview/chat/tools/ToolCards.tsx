import { useEffect, useRef, useState } from "react";
import { useLocale } from "../../../i18n";
import { useToolCopy } from "./copy";
import { demoSample } from "./model";
import {
  ToolIcon,
  DemoDisabledButton,
  ToolCard,
  chipClass,
  hintClass,
  primaryBtnClass,
  sectionLabelClass,
} from "./ui";

// 搜索 / 文件解析 / 代码沙箱。全部为固定示例，不发起网络请求、不读取文件、
// 不执行代码；任何声称会写入数据的按钮都是禁用态。

export function WebSearchCard({ keyword }: { keyword: string }) {
  const t = useToolCopy();
  const sample = demoSample(useLocale());
  const [visible, setVisible] = useState(3);
  const shown = sample.searchResults.slice(0, visible);
  const shownKeyword = keyword.trim() || sample.searchKeyword;
  return (
    <ToolCard
      icon={<ToolIcon name="search" size={15} />}
      title={t("card.search")}
      detail={t("search.count", { count: sample.searchResults.length })}
    >
      <p className={hintClass}>{t("search.keyword", { keyword: shownKeyword })}</p>
      <ul className="mt-3 flex flex-col gap-2">
        {shown.map((item) => (
          <li key={item.title} className="rounded-lg bg-[var(--ia-bg)] px-3 py-2.5">
            <p className="text-[13px] font-medium text-[var(--blue)]">{item.title}</p>
            <p className="mt-1 text-xs leading-relaxed text-[var(--ia-muted)]">{item.summary}</p>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
        <span className={hintClass}>
          {t("search.shown", { shown: shown.length, total: sample.searchResults.length })}
        </span>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-surface)] px-3 py-1.5 text-[13px] font-medium text-[var(--ia-muted)] disabled:opacity-50"
          disabled={visible >= sample.searchResults.length}
          onClick={() =>
            setVisible((current) => Math.min(current + 2, sample.searchResults.length))
          }
        >
          {visible >= sample.searchResults.length ? t("search.done") : t("search.more")}
        </button>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[var(--ia-line)] pt-3">
        <DemoDisabledButton>{t("search.save")}</DemoDisabledButton>
        <DemoDisabledButton>{t("parse.edit")}</DemoDisabledButton>
        <span className={hintClass}>{t("demo.allWritesDisabled")}</span>
      </div>
    </ToolCard>
  );
}

type ParseState = "parsing" | "done" | "failed";

export function FileParseCard() {
  const t = useToolCopy();
  const sample = demoSample(useLocale());
  const [state, setState] = useState<ParseState>("parsing");
  const [progress, setProgress] = useState(8);

  useEffect(() => {
    if (state !== "parsing") return;
    const id = window.setInterval(() => {
      setProgress((current) => Math.min(current + 12, 100));
    }, 220);
    return () => window.clearInterval(id);
  }, [state]);

  useEffect(() => {
    if (state === "parsing" && progress >= 100) setState("done");
  }, [state, progress]);

  const badgeText =
    state === "parsing"
      ? t("parse.parsing", { percent: progress })
      : state === "done"
        ? t("parse.done")
        : t("parse.failed");
  const badgeColor =
    state === "parsing"
      ? "bg-[var(--ia-sand)] text-[var(--ia-amber)]"
      : state === "done"
        ? "bg-[var(--ia-mint)] text-[var(--ia-green)]"
        : "bg-[var(--red-bg)] text-[var(--red)]";

  return (
    <ToolCard
      icon={<ToolIcon name="fileText" size={15} />}
      title={t("card.parse")}
      detail={
        <span className={`inline-flex items-center gap-1 ${chipClass} ${badgeColor}`}>
          {state === "parsing" && <ToolIcon name="loader" size={11} className="animate-spin" />}
          {badgeText}
        </span>
      }
    >
      <p className={hintClass}>{sample.parsedFileName}</p>
      {state === "parsing" && (
        <div className="flex flex-col gap-1.5">
          <div className="h-1.5 overflow-hidden rounded-full bg-[var(--ia-line)]">
            <div
              className="ia-tool-parsing-track h-full rounded-full transition-[width] duration-200"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className={hintClass}>
            {t("parse.progress", { page: Math.max(1, Math.ceil(progress / 20)) })}
          </p>
        </div>
      )}
      {state === "done" && (
        <div className="flex flex-col gap-3">
          <p className={sectionLabelClass}>{t("parse.projects")}</p>
          {sample.parsedProjects.map((project) => (
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
          <p className={sectionLabelClass}>{t("parse.skills")}</p>
          <div className="flex flex-wrap gap-1.5">
            {sample.parsedSkills.map((skill) => (
              <span key={skill} className={chipClass + " bg-[var(--ia-bg)] text-[var(--ia-muted)]"}>
                {skill}
              </span>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <DemoDisabledButton>{t("parse.save")}</DemoDisabledButton>
            <DemoDisabledButton>{t("parse.edit")}</DemoDisabledButton>
          </div>
        </div>
      )}
      {state === "failed" && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="flex items-center gap-1.5 text-xs text-[var(--red)]">
            <ToolIcon name="triangleAlert" size={13} />
            {t("parse.failReason")}
          </p>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-surface)] px-3 py-1.5 text-[13px] font-medium text-[var(--ia-muted)]"
            onClick={() => {
              setProgress(8);
              setState("parsing");
            }}
          >
            <ToolIcon name="rotateCcw" size={12} />
            {t("parse.retry")}
          </button>
        </div>
      )}
      <p className={hintClass + " flex flex-wrap items-center justify-between gap-2"}>
        <span>{t("parse.demoHint")}</span>
        {state === "done" && (
          <button
            type="button"
            className="text-[var(--ia-muted)] underline decoration-dotted"
            onClick={() => setState("failed")}
          >
            {t("parse.simulateFail")}
          </button>
        )}
      </p>
    </ToolCard>
  );
}

export function CodeSandboxCard() {
  const t = useToolCopy();
  const sample = demoSample(useLocale());
  const [code, setCode] = useState(sample.sandboxCode);
  const [running, setRunning] = useState(false);
  const [ran, setRan] = useState(false);
  const timer = useRef<number>();

  useEffect(() => {
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, []);

  return (
    <ToolCard
      icon={<ToolIcon name="squareTerminal" size={15} />}
      title={t("card.sandbox")}
      detail={
        <span className={chipClass + " bg-[var(--ia-bg)] text-[var(--ia-muted)]"}>
          {sample.sandboxLanguage}
        </span>
      }
    >
      <label className={"mb-1 block " + hintClass} htmlFor="ia-demo-code">
        {t("sandbox.codeLabel")}
      </label>
      <textarea
        id="ia-demo-code"
        spellCheck={false}
        value={code}
        rows={8}
        onChange={(event) => setCode(event.target.value)}
        className="w-full resize-y rounded-lg bg-[var(--navy)] p-3 font-mono text-xs leading-relaxed text-[#e7eaf0] outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--ia-accent)]"
      />
      <div className="flex flex-wrap items-center gap-2.5">
        <button
          type="button"
          className={primaryBtnClass}
          disabled={running}
          onClick={() => {
            setRunning(true);
            setRan(false);
            if (timer.current) window.clearTimeout(timer.current);
            timer.current = window.setTimeout(() => {
              setRunning(false);
              setRan(true);
            }, 600);
          }}
        >
          {running ? <ToolIcon name="loader" size={13} className="animate-spin" /> : null}
          {running ? t("sandbox.running") : t("sandbox.run")}
        </button>
        <span className={chipClass + " bg-[var(--ia-bg)] text-[var(--ia-muted)]"}>
          {t("sandbox.cases", { count: sample.sandboxCases.length })}
        </span>
        {ran && <span className={hintClass}>{t("sandbox.runNumber", { number: 1 })}</span>}
      </div>
      {ran && (
        <ul className="flex flex-col gap-1.5">
          {sample.sandboxCases.map((item, index) => (
            <li key={item.input} className="rounded-lg bg-[var(--ia-bg)] px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-[var(--ia-muted)]">
                  {t("sandbox.case", { number: index + 1 })}
                </span>
                <span className={chipClass + " ml-auto bg-[var(--ia-mint)] text-[var(--ia-green)]"}>
                  {item.pass ? t("sandbox.pass") : t("sandbox.fail")}
                </span>
              </div>
              <p className="mt-1 font-mono text-xs text-[var(--ia-muted)]">
                {t("sandbox.input")}: {item.input} · {t("sandbox.expect")}: {item.expect} ·{" "}
                {t("sandbox.actual")}: {item.actual}
              </p>
            </li>
          ))}
          <li className={hintClass}>{t("sandbox.resultHint")}</li>
        </ul>
      )}
      <div className="rounded-lg bg-[var(--ia-tint)] px-3 py-2.5">
        <p className="text-xs font-semibold text-[var(--ia-accent)]">{t("sandbox.analysis")}</p>
        <p className="mt-1 text-xs leading-relaxed text-[var(--ia-muted)]">
          {sample.sandboxAnalysis}
        </p>
      </div>
    </ToolCard>
  );
}
