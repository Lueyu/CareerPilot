import { getLocale, useT } from '../i18n';
// 语音转文字（STT）：基于浏览器内置 Web Speech API，纯前端、免费、不依赖后端。
// 仅 Chromium 系（Chrome / Edge）支持；Firefox / Safari 不支持（supported 返回 false）。
import { useCallback, useEffect, useRef, useState } from 'react';

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechRecognitionResultEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorLike) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

interface SpeechRecognitionResultLike {
  isFinal: boolean;
  length: number;
  [index: number]: { transcript: string };
}

interface SpeechRecognitionResultEventLike {
  resultIndex: number;
  results: { length: number; [index: number]: SpeechRecognitionResultLike };
}

interface SpeechRecognitionErrorLike {
  error?: string;
  message?: string;
}

function getCtor(): SpeechRecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as Record<string, unknown>;
  return (w.SpeechRecognition || w.webkitSpeechRecognition || null) as SpeechRecognitionCtor | null;
}

export function isSpeechSupported(): boolean {
  return getCtor() !== null;
}

export interface SpeechRecognitionState {
  supported: boolean;
  listening: boolean;
  /** 尚未定稿的中间识别结果，仅用于实时预览。 */
  interim: string;
  error: string | null;
  start: () => void;
  stop: () => void;
}

/**
 * 语音识别 hook。每段「最终结果」以 delta 形式回调（同一段文本只回调一次），
 * 组件据此把文本追加到回答框；`interim` 是中间结果，仅用于预览。
 */
export function useSpeechRecognition(onFinalDelta: (delta: string) => void): SpeechRecognitionState {
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState('');
  const t = useT();
  const [error, setError] = useState<{ key: string; reason?: string } | null>(null);
  const recRef = useRef<SpeechRecognitionLike | null>(null);
  const onDeltaRef = useRef(onFinalDelta);
  onDeltaRef.current = onFinalDelta;

  const stop = useCallback(() => {
    const rec = recRef.current;
    // A stop request can still produce a final result before onend. Keep the
    // consumer locked until that result has been delivered.
    if (rec) { try { rec.stop(); } catch { setListening(false); } }
    else setListening(false);
  }, []);

  const start = useCallback(() => {
    const Ctor = getCtor();
    if (!Ctor) { setError({ key: "study.speechUnsupported" }); return; }
    if (recRef.current) { try { recRef.current.abort(); } catch { /* noop */ } }

    const rec = new Ctor();
    rec.lang = getLocale();
    rec.continuous = true;
    rec.interimResults = true;

    rec.onresult = (event) => {
      if(recRef.current!==rec)return;
      let interimText = '';
      let delta = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const res = event.results[i];
        const text = res[0]?.transcript ?? '';
        if (res.isFinal) delta += text;
        else interimText += text;
      }
      setInterim(interimText);
      if (delta) onDeltaRef.current(delta);
    };

    rec.onerror = (event) => {
      if(recRef.current!==rec)return;
      const code = event?.error;
      if (code === 'not-allowed' || code === 'service-not-allowed') {
        setError({ key: "study.microphoneDenied" });
      } else if (code === 'no-speech') {
        setError({ key: "study.speechNotDetected" });
      } else if (code !== 'aborted') {
        setError({ key: "study.speechRecognitionError", reason: code || event?.message });
      }
    };

    rec.onend = () => {
      if(recRef.current!==rec)return;
      setListening(false);
      setInterim('');
      recRef.current = null;
    };

    recRef.current = rec;
    setError(null);
    setInterim('');
    try {
      rec.start();
      setListening(true);
    } catch (err) {
      setError({ key: "study.speechStartFailed", reason: err instanceof Error ? err.message : String(err) });
      recRef.current = null;
    }
  }, []);

  // 卸载时中止识别，避免组件销毁后回调触发 setState。
  useEffect(() => () => { const rec=recRef.current;recRef.current=null;try { rec?.abort(); } catch { /* noop */ } }, []);

  return { supported: isSpeechSupported(), listening, interim, error: error ? t(error.key, { reason: error.reason || t("study.unknownError") }) : null, start, stop };
}
