import { useEffect, useRef, useState } from "react";
import { Mic, Pause, Play, Square } from "lucide-react";
import { chipClass, ghostBtnClass } from "./ui";

// ===== 语音输入栏（底部输入栏内） =====
// 点击开始录音（mock），波形 + 计时 + 实时转写逐步显现；停止后把转写文本填入输入框。

const MOCK_TRANSCRIPT = "帮我介绍一下 Redis 的 RDB 和 AOF 有什么区别，以及生产上应该怎么选。";

const WAVE_HEIGHTS = [6, 12, 18, 10, 22, 14, 8, 16, 9, 13, 7, 11, 18, 8];

export function VoiceInputBar({ onTranscript }: { onTranscript: (text: string) => void }) {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [transcript, setTranscript] = useState("");
  const timer = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => {
    if (!recording) return;
    timer.current = setInterval(() => {
      setSeconds((value) => value + 1);
      setTranscript(MOCK_TRANSCRIPT.slice(0, Math.round((seconds + 1) * 4.5)));
    }, 1000);
    return () => clearInterval(timer.current);
  }, [recording, seconds]);

  function stop() {
    setRecording(false);
    clearInterval(timer.current);
    const text = transcript || MOCK_TRANSCRIPT;
    if (text.trim()) onTranscript(text);
    setTranscript("");
    setSeconds(0);
  }

  function toggle() {
    if (recording) {
      stop();
      return;
    }
    setSeconds(0);
    setTranscript("");
    setRecording(true);
  }

  return (
    <div
      className={
        "flex flex-col gap-2 rounded-xl border bg-[var(--ia-surface)] p-2.5 transition-colors " +
        (recording ? "border-[var(--ia-accent)]" : "border-[var(--ia-line)]")
      }
    >
      <div className="flex items-center gap-2.5">
        <button
          type="button"
          aria-label={recording ? "停止录音" : "开始语音输入"}
          aria-pressed={recording}
          onClick={toggle}
          className={
            "flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors " +
            (recording
              ? "ia-tool-recording bg-[var(--ia-accent)] text-white"
              : "bg-[var(--ia-bg)] text-[var(--ia-muted)] hover:text-[var(--ia-accent)]")
          }
        >
          {recording ? <Square size={13} /> : <Mic size={15} />}
        </button>
        {recording ? (
          <>
            <span className="flex items-center gap-[3px]" aria-hidden="true">
              {WAVE_HEIGHTS.map((height, index) => (
                <span
                  key={index}
                  className="ia-tool-wave-bar block w-[3px] rounded-full bg-[var(--ia-accent)]"
                  style={{ height, animationDelay: `${index * 0.09}s` }}
                />
              ))}
            </span>
            <span className={chipClass + " bg-[var(--red-bg)] text-[var(--red)]"}>
              录音中 0:{String(seconds).padStart(2, "0")}
            </span>
            <span className="ml-auto text-[11px] text-[var(--ia-muted)]">停顿 1s 自动断句</span>
          </>
        ) : (
          <>
            <span className="text-xs text-[var(--ia-muted)]">
              点击说话，语音会实时转写为文字
            </span>
            <span className="ml-auto text-[11px] text-[var(--ia-muted)] opacity-70">
              支持普通话 / 英语
            </span>
          </>
        )}
      </div>
      {recording && (
        <p className="rounded-lg bg-[var(--ia-bg)] px-3 py-2 text-xs leading-relaxed text-[var(--ia-ink)]">
          {transcript || "正在聆听…"}
          <span className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-0.5 animate-pulse bg-[var(--ia-accent)]" />
        </p>
      )}
    </div>
  );
}

// ===== 语音播放按钮（AI 消息底部） =====
// mock 朗读：按内容长度估算时长，点击后模拟播放进度。

export function VoicePlayButton({ text }: { text: string }) {
  const [playing, setPlaying] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const duration = Math.max(8, Math.min(60, Math.round(text.length / 12)));
  const timer = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => {
    if (!playing) return;
    timer.current = setInterval(() => {
      setSeconds((value) => {
        if (value + 1 >= duration) {
          clearInterval(timer.current);
          setPlaying(false);
          return 0;
        }
        return value + 1;
      });
    }, 1000);
    return () => clearInterval(timer.current);
  }, [playing, duration]);

  const format = (value: number) => `0:${String(value).padStart(2, "0")}`;

  return (
    <button
      type="button"
      className={ghostBtnClass + " rounded-full py-1 text-xs"}
      aria-label={playing ? "停止朗读" : "朗读这条回复"}
      onClick={() => {
        if (playing) {
          setPlaying(false);
          setSeconds(0);
          clearInterval(timer.current);
        } else {
          setSeconds(0);
          setPlaying(true);
        }
      }}
    >
      {playing ? (
        <Pause size={12} className="text-[var(--ia-accent)]" />
      ) : (
        <Play size={12} className="text-[var(--ia-accent)]" />
      )}
      {playing ? "正在朗读" : "朗读"}
      {playing && (
        <span className="inline-flex items-center gap-1.5">
          <span className="relative block h-1 w-16 overflow-hidden rounded-full bg-[var(--ia-tint)]">
            <span
              className="absolute inset-y-0 left-0 rounded-full bg-[var(--ia-accent)] transition-[width] duration-1000"
              style={{ width: `${(seconds / duration) * 100}%` }}
            />
          </span>
          <span className="text-[10.5px] text-[var(--ia-muted)]">
            {format(seconds)} / {format(duration)}
          </span>
        </span>
      )}
    </button>
  );
}
