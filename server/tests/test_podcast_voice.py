import math
import struct
import sys
from types import SimpleNamespace

import pytest

from app.services import podcast_voice as voice


def test_bilingual_phrases_preserve_technical_terms_and_select_narrators():
    text = '先验证 FastAPI v2.1 的幂等键。\nUse Redis, e.g. for retries. Keep the gRPC method name.'
    assert list(voice._speech_phrases(text, "bilingual")) == [
        ("先验证 FastAPI v2.1 的幂等键。", 280, "zh"),
        ("Use Redis, e.g. for retries.", 140, "en"),
        ("Keep the gRPC method name.", 280, "en"),
    ]
    assert {language for _, _, language in voice._speech_phrases(text, "en")} == {"en"}


def test_long_english_phrase_keeps_word_boundaries():
    text = "Retain the HTTP idempotency key during retries " * 12
    phrases = [phrase for phrase, _, _ in voice._speech_phrases(text, "en")]
    assert len(phrases) > 1
    assert all(len(phrase) <= voice._MAX_PHRASE_CHARS for phrase in phrases)
    assert " ".join(phrases) == text.strip()


def test_bilingual_fallback_uses_both_locales_in_one_mp3(monkeypatch, tmp_path):
    monkeypatch.setenv("CAREERPILOT_PODCAST_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(voice, "sys", SimpleNamespace(platform="linux"))
    rendered_languages = []
    pcm = b"".join(struct.pack("<h", int(3000 * math.sin(i / 10))) for i in range(22050))

    def speak(text, language):
        rendered_languages.append(language)
        return pcm, 22050

    monkeypatch.setattr(voice, "_espeak_pcm", speak)
    audio, duration = voice.render_mp3("先看幂等键。\nThen check the event log.", "bilingual")
    assert rendered_languages == ["zh", "en"]
    assert audio.startswith(b"\xff\xf3")
    assert 2.28 < duration < 2.5


@pytest.mark.skipif(sys.platform != "win32", reason="Windows SAPI voice selection")
def test_sapi_english_request_with_only_a_mandarin_voice_fails(monkeypatch, tmp_path):
    from win32com.client import dynamic

    monkeypatch.setenv("CAREERPILOT_PODCAST_MODEL_DIR", str(tmp_path))
    mandarin_voice = SimpleNamespace(GetAttribute=lambda _: "0804")
    sapi = SimpleNamespace(GetVoices=lambda: [mandarin_voice])
    monkeypatch.setattr(dynamic, "Dispatch", lambda _: sapi)
    with pytest.raises(voice.PodcastAudioError, match="没有可用的英语"):
        voice.render_mp3("Explain how an API handles retries.", "en")


@pytest.mark.asyncio
async def test_worker_rejects_unsupported_language_before_starting():
    with pytest.raises(voice.PodcastAudioError, match="bilingual"):
        await voice.render_mp3_in_worker("Speak this sentence.", "fr")
