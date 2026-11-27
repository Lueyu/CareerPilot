import { ToolIcon } from "./ui";
import { useToolCopy } from "./copy";

// 语音输入与朗读尚未接入。这里只展示禁用态与说明，不调用麦克风、
// 不伪造转写文本、不模拟播放进度，也不会向输入框写入任何内容。

export function VoiceInputControl() {
  const t = useToolCopy();
  return (
    <button
      type="button"
      disabled
      aria-disabled="true"
      title={t("voice.inputHint")}
      className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-bg)] px-3 py-1.5 text-xs font-medium text-[var(--ia-muted)] opacity-70"
    >
      <ToolIcon name="mic" size={13} />
      {t("voice.input")}
      <span className="text-[11px]">· {t("demo.notWired")}</span>
    </button>
  );
}

export function VoicePlayControl() {
  const t = useToolCopy();
  return (
    <button
      type="button"
      disabled
      aria-disabled="true"
      title={t("voice.playHint")}
      className="inline-flex items-center gap-1.5 rounded-full border border-[var(--ia-line)] bg-[var(--ia-bg)] px-3 py-1 text-xs text-[var(--ia-muted)] opacity-70"
    >
      <ToolIcon name="volume2" size={12} />
      {t("voice.play")}
    </button>
  );
}
