import { useEffect, useState, type FormEvent } from "react";
import { useLocale } from "../../../i18n";
import { toolReply, useToolCopy } from "./copy";
import { CompressionNotice, MemoryIndicator } from "./Context";
import { DEMO_CONTEXT_RESET_EVENT, isDemoContextReset } from "./contextStore";
import { CalendarCard, MindMapCard } from "./KnowledgeCards";
import { demoSample, detectToolIntent, type ToolCardKind, type ToolIntent } from "./model";
import { SkillProgressCard, defaultSkillParams, type SkillParams } from "./SkillProgressCard";
import { DiffCard, TranslateCard } from "./TextCards";
import { CodeSandboxCard, FileParseCard, WebSearchCard } from "./ToolCards";
import { DemoBanner, ToolIcon } from "./ui";

// 按意图分发到对应的固定示例工具卡。所有卡片只读取 model.ts 的示例数据。
export function ToolCardView({ kind, raw }: { kind: ToolCardKind; raw?: string }) {
  const locale = useLocale();
  const [params, setParams] = useState<SkillParams>(defaultSkillParams);
  const [status, setStatus] = useState<"running" | "paused">("running");
  switch (kind) {
    case "search": {
      const keyword =
        raw
          ?.replace(
            /^(?:请|帮我)?(?:联网)?(?:搜(?:一)?下|搜索一下?|查(?:一)?查|找(?:一)?下)[:：,，\s]*/i,
            "",
          )
          .trim() || demoSample(locale).searchKeyword;
      return <WebSearchCard keyword={keyword} />;
    }
    case "parse":
      return <FileParseCard />;
    case "sandbox":
      return <CodeSandboxCard />;
    case "mindmap":
      return <MindMapCard />;
    case "calendar":
      return <CalendarCard />;
    case "translate":
      return <TranslateCard />;
    case "diff":
      return <DiffCard />;
    case "skill":
      return (
        <SkillProgressCard
          params={params}
          onParamsChange={setParams}
          status={status}
          onStatusChange={setStatus}
        />
      );
    default:
      return null;
  }
}

const intents: ToolIntent[] = [
  "search",
  "parse",
  "sandbox",
  "mindmap",
  "calendar",
  "translate",
  "diff",
  "skill",
];

type DemoTurn = {
  id: number;
  kind: "sampleSearch" | "sampleCard" | "user";
  raw: string;
  intent: ToolIntent | null;
};

const firstTurn: DemoTurn = { id: -1, kind: "sampleSearch", raw: "", intent: "search" };

/** 显式 Demo 工作区专用。独立演示输入，不接管真实 Composer，也不写入任何记录。 */
export function DemoToolLayer() {
  const t = useToolCopy();
  const locale = useLocale();
  const [turn, setTurn] = useState<DemoTurn | null>(() =>
    isDemoContextReset() ? null : firstTurn,
  );
  const [draft, setDraft] = useState("");
  const [reset, setReset] = useState(() => isDemoContextReset());

  useEffect(() => {
    const onReset = () => {
      setTurn(null);
      setDraft("");
      setReset(true);
    };
    window.addEventListener(DEMO_CONTEXT_RESET_EVENT, onReset);
    return () => window.removeEventListener(DEMO_CONTEXT_RESET_EVENT, onReset);
  }, []);

  function show(next: DemoTurn) {
    setReset(false);
    setTurn(next);
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const raw = draft.trim();
    if (!raw) return;
    show({
      id: -Date.now(),
      kind: "user",
      raw,
      intent: detectToolIntent(raw),
    });
    setDraft("");
  }

  const prompt =
    turn?.kind === "sampleSearch"
      ? t("demo.samplePrompt")
      : turn?.kind === "sampleCard" && turn.intent
        ? t("demo.turnLabel", { card: t("card." + turn.intent) })
        : (turn?.raw ?? "");

  return (
    <section className="ia-tools-demo-layer" aria-label={t("demo.onlyDemo")}>
      <DemoBanner action={<MemoryIndicator />} />
      {reset && (
        <p className="ia-tools-demo-reset" role="status">
          {t("demo.resetEmpty")}
        </p>
      )}
      {turn && (
        <div className="ia-tools-demo-turn">
          <p className="ia-tools-demo-user">{prompt}</p>
          {turn.intent ? (
            <article className="ia-tools-demo-reply">
              <div className="ia-tools-demo-reply-head">
                <span className="ia-tools-demo-reply-tag">{t("demo.reply")}</span>
                <span>{toolReply(turn.intent, locale)}</span>
              </div>
              <p className="ia-tools-demo-note">{t("demo.cardNote")}</p>
              <ToolCardView kind={turn.intent} raw={turn.raw} />
            </article>
          ) : (
            <p className="ia-tools-demo-nomatch" role="status">
              {t("demo.noMatch")}
            </p>
          )}
        </div>
      )}
      <CompressionNotice />
      <form className="ia-tools-demo-form" onSubmit={onSubmit}>
        <label className="ia-tools-demo-label" htmlFor="ia-tools-demo-input">
          {t("demo.inputLabel")}
        </label>
        <div className="ia-tools-demo-form-row">
          <input
            id="ia-tools-demo-input"
            value={draft}
            maxLength={200}
            placeholder={t("demo.inputPlaceholder")}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button type="submit" disabled={!draft.trim()}>
            <ToolIcon name="arrowRightLeft" size={13} />
            {t("demo.showSample")}
          </button>
        </div>
        <div className="ia-tools-demo-chips" aria-label={t("demo.samples")}>
          {intents.map((intent) => (
            <button
              key={intent}
              type="button"
              aria-pressed={turn?.kind === "sampleCard" && turn.intent === intent}
              onClick={() => show({ id: -Date.now(), kind: "sampleCard", raw: "", intent })}
            >
              {t("card." + intent)}
            </button>
          ))}
        </div>
        <p className="ia-tools-demo-hint">{t("demo.hint")}</p>
      </form>
    </section>
  );
}
