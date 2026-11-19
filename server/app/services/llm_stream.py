"""Bounded Chat Completions SSE reader. No partial result or reasoning retention."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time

import httpx

from app.services.project_autopilot_sources import safe_request_id


@dataclass(frozen=True)
class StreamLimits:
    events: int = 100_000
    wire_bytes: int = 16 * 1024 * 1024
    content_bytes: int = 1024 * 1024
    event_bytes: int = 256 * 1024


DEFAULT_LIMITS = StreamLimits()


class StreamFailure(ValueError):
    """Only fixed local codes, never provider data, are exposed to diagnostics."""
    def __init__(self, code):
        super().__init__(code)
        self.kind = 'network' if code in {'missing_done', 'incomplete_event'} else 'invalid_response'


class ChatCompletionStream:
    def __init__(self, meta: dict, started_at: float):
        self.meta, self.started_at = meta, started_at
        self.limits = DEFAULT_LIMITS
        self.usage = None
        self.reported_model = None
        self.parts: list[str] = []
        self.content_bytes = 0
        self.finish = None
        self.done = False
        self.meta.update(transport_mode='sse-v1', stream_done=False, sse_event_count=0,
                         sse_data_event_count=0, sse_events_before_content=0,
                         stream_wire_bytes=0, stream_decoded_bytes=0)

    def elapsed(self):
        return int((time.perf_counter() - self.started_at) * 1000)

    def fail(self, code):
        self.meta['stream_error'] = code
        self.parts.clear()
        raise StreamFailure(code)

    def dispatch(self, lines: list[bytes], event_type: bytes | None):
        self.meta['sse_event_count'] += 1
        if self.meta['sse_event_count'] > self.limits.events:
            self.fail('event_count_limit')
        if event_type not in (None, b'', b'message'):
            self.fail('provider_error' if event_type == b'error' else 'unsupported_event')
        if not lines:
            return  # SSE comments/heartbeats are bounded too, and do not reset the total deadline.
        previous_event_ms = self.meta.get('last_sse_ms')
        event_ms = self.elapsed()
        self.meta.setdefault('first_sse_ms', event_ms)
        self.meta['last_sse_ms'] = event_ms
        self.meta['sse_data_event_count'] += 1
        if 'first_content_ms' not in self.meta:
            self.meta['sse_events_before_content'] = self.meta['sse_data_event_count']
            self.meta['last_sse_before_content_ms'] = event_ms
        try:
            data = b'\n'.join(lines).decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            self.fail('invalid_utf8')
        if not data:
            return
        if data == '[DONE]':
            if self.finish != 'stop':
                self.fail('missing_finish')
            if not self.parts or not any(part.strip() for part in self.parts):
                self.fail('empty_content')
            self.done = True
            self.meta.update(stream_done=True, stream_done_ms=self.elapsed())
            return
        try:
            value = json.loads(data)
        except (ValueError, RecursionError):
            self.fail('invalid_json')
        # The event is transient. Never copy it, its delta, or reasoning fields to state/logs.
        if not isinstance(value, dict):
            self.fail('invalid_json')
        if value.get('error') is not None:
            self.fail('provider_error')
        if isinstance(value.get('model'), str):
            self.reported_model = value['model'][:200]
        usage = value.get('usage')
        if isinstance(usage, dict):
            safe_usage = {k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                          if type(usage.get(k)) is int and usage[k] >= 0}
            if safe_usage:
                self.usage = safe_usage
        if not self.meta.get('request_id'):
            request_id = safe_request_id(value.get('id'))
            if request_id:
                self.meta['request_id'] = request_id
        choices = value.get('choices')
        if not isinstance(choices, list) or len(choices) > 1:
            self.fail('invalid_choice')
        if not choices:
            if isinstance(usage, dict):
                return  # Optional usage-only terminal event; no stream_options requested.
            self.fail('invalid_choice')
        choice = choices[0]
        if not isinstance(choice, dict) or type(choice.get('index')) is not int or choice['index'] != 0:
            self.fail('invalid_choice')
        if self.finish is not None:
            self.fail('content_after_finish')
        delta = choice.get('delta')
        if not isinstance(delta, dict):
            self.fail('invalid_delta')
        if any(delta.get(k) is not None for k in ('tool_calls', 'function_call', 'refusal')):
            self.fail('unsupported_delta')
        content = delta.get('content')
        if content is not None and not isinstance(content, str):
            self.fail('invalid_content')
        if content:
            try:
                size = len(content.encode('utf-8'))
            except UnicodeEncodeError:
                self.fail('invalid_content')
            if self.content_bytes + size > self.limits.content_bytes:
                self.fail('content_limit')
            self.content_bytes += size
            self.parts.append(content)
            if 'first_content_ms' not in self.meta:
                self.meta['sse_events_before_content'] = self.meta['sse_data_event_count'] - 1
                if previous_event_ms is None:
                    self.meta.pop('last_sse_before_content_ms', None)
                else:
                    self.meta['last_sse_before_content_ms'] = previous_event_ms
            self.meta.setdefault('first_content_ms', self.elapsed())
        # All other fields, including reasoning/reasoning_content, are discarded.
        finish = choice.get('finish_reason')
        if finish is not None:
            if finish in ('stop', 'length', 'content_filter', 'tool_calls', 'function_call'):
                self.meta['finish_reason'] = finish
            if finish == 'length':
                self.fail('output_truncated')
            if finish != 'stop':
                self.fail('unsupported_finish')
            self.finish = finish
            self.meta['stream_finish_ms'] = self.elapsed()

    async def read(self, response: httpx.Response, on_content=None):
        if response.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'text/event-stream':
            self.fail('unsupported_content_type')
        line = bytearray()
        data_lines: list[bytes] = []
        event_type = None
        event_bytes = 0
        try:
            # Decode HTTP transfer/content encodings using HTTPX, but bound bytes before
            # accumulating SSE lines. aiter_lines would buffer an unbounded provider line.
            async for chunk in response.aiter_bytes():
                self.meta['stream_wire_bytes'] = response.num_bytes_downloaded
                self.meta['stream_decoded_bytes'] += len(chunk)
                if max(self.meta['stream_wire_bytes'], self.meta['stream_decoded_bytes']) > self.limits.wire_bytes:
                    self.fail('wire_limit')
                pos = 0
                while pos < len(chunk):
                    newline = chunk.find(b'\n', pos)
                    end = len(chunk) if newline < 0 else newline
                    size = end - pos
                    if event_bytes + len(line) + size + (newline >= 0) > self.limits.event_bytes:
                        self.fail('event_limit')
                    line.extend(chunk[pos:end])
                    pos = end + 1
                    if newline < 0:
                        break
                    part = bytes(line)
                    raw_line_bytes = len(part) + 1
                    line.clear()
                    if part.endswith(b'\r'):
                        part = part[:-1]
                    if not part:
                        previous_parts = len(self.parts)
                        self.dispatch(data_lines, event_type)
                        if on_content and len(self.parts) > previous_parts:
                            await on_content(self.parts[-1])
                        data_lines.clear()
                        event_type, event_bytes = None, 0
                        if self.done:
                            content = ''.join(self.parts)
                            self.meta.update(response_body_complete=True, transport_phase='stream_complete',
                                response_body_ms=self.elapsed(), output_chars=len(content), output_bytes=self.content_bytes,
                                output_sha256=hashlib.sha256(content.encode('utf-8')).hexdigest())
                            return {'content': content, **({'usage': self.usage} if self.usage else {})}
                        continue
                    event_bytes += raw_line_bytes
                    if part.startswith(b'data:') or part == b'data':
                        value = part[5:]
                        data_lines.append(value[1:] if value.startswith(b' ') else value)
                    elif part.startswith(b'event:'):
                        event_type = part[6:].lstrip(b' ')
                    # Other SSE fields and comments have no effect; no automatic reconnect.
            if line or data_lines or event_bytes:
                self.fail('incomplete_event')
            self.fail('missing_done')
        finally:
            self.parts.clear()
