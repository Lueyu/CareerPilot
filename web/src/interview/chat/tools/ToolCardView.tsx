import { intentReplies, type ToolCardKind } from "./model";
import { CodeSandboxCard, FileParseCard, WebSearchCard } from "./ToolCards";
import { CalendarCard, MindMapCard } from "./KnowledgeCards";
import { DiffCard, TranslateCard } from "./TextCards";
import { SkillProgressCard, type SkillParams } from "./SkillProgressCard";

// 按意图分发到对应的 mock 工具卡。

export function ToolCardView({
  kind,
  raw,
  skillParams,
  onSkillParamsChange,
  skillStatus,
  onSkillStatusChange,
}: {
  kind: ToolCardKind;
  raw?: string;
  skillParams?: SkillParams;
  onSkillParamsChange?: (next: SkillParams) => void;
  skillStatus?: "running" | "paused";
  onSkillStatusChange?: (next: "running" | "paused") => void;
}) {
  switch (kind) {
    case "search": {
      const keyword =
        raw
          ?.replace(
            /^(?:请|帮我)?(?:联网)?(?:搜(?:一)?下|搜索一下?|查(?:一)?查|找(?:一)?下)[:：,，\s]*/i,
            "",
          )
          .trim() || "字节跳动 后端 面经";
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
      return skillParams ? (
        <SkillProgressCard
          params={skillParams}
          onParamsChange={(next) => onSkillParamsChange?.(next)}
          status={skillStatus ?? "running"}
          onStatusChange={(next) => onSkillStatusChange?.(next)}
        />
      ) : null;
    default:
      return <p className="text-xs text-[var(--ia-muted)]">{intentReplies[kind]}</p>;
  }
}
