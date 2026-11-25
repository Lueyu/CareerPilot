import { useCallback, useEffect, useRef, useState } from "react";
import { api, readMessageStream, type MessageStreamEvent } from "../../api/client";
import { changeEvent, errorText, type Session } from "./model";

export const sessionsPath = "/study/sessions";
export const sessionPath = (id: number) => sessionsPath + "/" + id;

export function notifyHistory() {
  window.dispatchEvent(new Event(changeEvent));
}

export function useConversation(sessionId?: number) {
  const [session, setSession] = useState<Session>();
  const [error, setError] = useState("");
  const [stream, setStream] = useState<MessageStreamEvent>();
  const sequence = useRef(0);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    const version = ++sequence.current;
    try {
      const data = await api.get<Session>(sessionPath(sessionId));
      if (sequence.current === version) {
        setSession(data);
        setError("");
      }
    } catch (err) {
      if (sequence.current === version) setError(errorText(err));
      throw err;
    }
  }, [sessionId]);

  const invalidate = useCallback(() => {
    sequence.current++;
  }, []);
  useEffect(() => {
    void refresh().catch(() => {});
    return invalidate;
  }, [refresh, invalidate]);

  const generating = session?.messages.find((message) => message.status === "generating");
  const messageId = generating?.id;
  const attempt = generating?.attempt;
  useEffect(() => {
    if (!sessionId || !messageId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      if (controller.signal.aborted) return;
      await refresh().catch(() => {});
      if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 2000);
    };
    void readMessageStream(
      sessionPath(sessionId) + "/messages/" + messageId + "/stream",
      controller.signal,
      (event) => {
        if (!controller.signal.aborted && event.id === messageId && event.attempt === attempt) {
          setStream(event);
        }
      },
    ).then(poll, poll);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [sessionId, messageId, attempt, refresh]);

  const messages =
    session?.messages.map((message) => {
      if (
        message.status !== "generating" ||
        stream?.id !== message.id ||
        stream.attempt !== message.attempt
      )
        return message;
      return { ...message, content: stream.content, error: stream.error };
    }) ?? [];

  return { session, generating, messages, error, refresh };
}
