"""Offline podcast speech synthesis. Text and audio buffers stay in memory."""
from __future__ import annotations

import asyncio
import io
import os
import re
import shutil
import signal
import subprocess
import sys
import wave
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path

_MP3_BIT_RATE = 64
_SAPI_PCM_RATE = 22050
_SAPI_PCM_FORMAT = 22  # SAFT22kHz16BitMono
_SAPI_PLAIN_TEXT = 16  # SVSFIsNotXML
_MANDARIN_LOCALES = ("804", "1004", "404")  # zh-CN, zh-SG, zh-TW
_SYNTHESIS_TIMEOUT = 120
_MAX_SYNTHESIS_TIMEOUT = 2400
_MODEL_DIR_ENV = "CAREERPILOT_PODCAST_MODEL_DIR"
_DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[3] / "personal/models/kokoro-int8-multi-lang-v1_1"
# Voice indices in the sherpa-onnx v1.1-zh export.
_KOKORO_SPEAKERS = {"zh": 3, "en": 0}
_LANGUAGE_NAMES = {"zh": "普通话", "en": "英语"}
_LANGUAGES = (*_LANGUAGE_NAMES, "bilingual")
_LEXICONS = ("lexicon-zh.txt", "lexicon-us-en.txt")
_RULES = ("phone-zh.fst", "date-zh.fst", "number-zh.fst")
_MODEL_FILES = ("model.int8.onnx", "voices.bin", "tokens.txt", *_LEXICONS, *_RULES)
# Keep periods inside names and decimal numbers, and keep closing quotes with their sentence.
_SENTENCE_BREAK = re.compile(
    r'(?<=[。！？!?])(?![!?！？"”’」』）])|(?<=[.!?。！？]["”’」』])\s*'
    r'|(?<=\.)(?<![A-Za-z]\.[A-Za-z]\.)\s+'
)
_CLAUSE_BREAK = re.compile(r"(?<=[，、；：])|(?<=[,;:])(?=\s)")
_WORD_BREAK = re.compile(r"\s+|(?<=[\u3400-\u9fff])(?=[\u3400-\u9fff])")
_HAN = re.compile(r"[\u3400-\u9fff]")
_MAX_PHRASE_CHARS = 180
_MAX_REQUEST_BYTES = 48_000
# Match the episode job's MP3 storage limit. Allow the larger uncompressed WAV.
_MAX_MP3_BYTES = 64 * 1024 * 1024
_MAX_WAV_BYTES = 384 * 1024 * 1024
_PIPE_CHUNK_SIZE = 64 * 1024
_MAX_ERROR_BYTES = 1024
_AUDIO_REPLY = b"\x00"
_ERROR_REPLY = b"\x01"
_WORKER_FAILED = "本地语音合成进程运行失败，请重试。"
_OUTPUT_TOO_LARGE = "本地语音合成的音频超过大小限制。"


class PodcastAudioError(ValueError):
    """A safe message the episode job can expose to the user."""


def _sapi_pcm(text: str, language: str) -> tuple[bytes, int]:
    name = _LANGUAGE_NAMES[language]
    try:
        import pythoncom
        from win32com.client.dynamic import Dispatch
    except ImportError:
        raise PodcastAudioError("缺少 Windows 语音组件 pywin32，请安装后重试。") from None

    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        try:
            voice = Dispatch("SAPI.SpVoice")
            voices = voice.GetVoices()
            selected = []
            for token in voices:
                locales = [
                    int(value, 16)
                    for value in token.GetAttribute("Language").split(";")
                    if re.fullmatch(r"[0-9a-fA-F]+", value.strip())
                ]
                if language == "en":
                    if any(locale & 0x3FF == 9 for locale in locales):
                        selected.append((int(0x409 not in locales), token))
                else:
                    for rank, locale in enumerate(_MANDARIN_LOCALES):
                        if int(locale, 16) in locales:
                            selected.append((rank, token))
                            break
            if not selected:
                raise PodcastAudioError(f"Windows 没有可用的{name} SAPI 语音，请安装{name}桌面语音后重试。")

            for _, token in sorted(selected, key=lambda item: item[0]):
                try:
                    voice.Voice = token
                    stream = Dispatch("SAPI.SpMemoryStream")
                    stream.Format.Type = _SAPI_PCM_FORMAT
                    voice.AudioOutputStream = stream
                    voice.Speak(text, _SAPI_PLAIN_TEXT)
                    return bytes(stream.GetData()), _SAPI_PCM_RATE
                except pythoncom.com_error:
                    continue
            raise PodcastAudioError(f"Windows {name}语音合成失败，请检查桌面语音安装后重试。")
        finally:
            pythoncom.CoUninitialize()
    except pythoncom.com_error:
        raise PodcastAudioError(f"Windows {name}语音合成失败，请检查桌面语音安装后重试。") from None


def _espeak_pcm(text: str, language: str) -> tuple[bytes, int]:
    name = _LANGUAGE_NAMES[language]
    failed = f"eSpeak NG {name}语音合成失败，请检查{name}语音后重试。"
    voice = "en-us" if language == "en" else "cmn"
    engine = shutil.which("espeak-ng")
    if not engine:
        raise PodcastAudioError(f"找不到离线{name}语音引擎，请安装带{name}语音的 eSpeak NG。")
    try:
        waveform = io.BytesIO()
        with subprocess.Popen(
            [engine, "--stdout", "-v", voice, "-b", "1", "--stdin"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            # With --stdin, eSpeak reads all input before emitting audio.
            with process.stdin:
                process.stdin.write(text.encode("utf-8"))
            while chunk := process.stdout.read(_PIPE_CHUNK_SIZE):
                if waveform.tell() + len(chunk) > _MAX_WAV_BYTES:
                    process.kill()
                    raise PodcastAudioError(_OUTPUT_TOO_LARGE)
                waveform.write(chunk)
            if process.wait() != 0:
                raise PodcastAudioError(failed)
        waveform.seek(0)
        with wave.open(waveform) as wav:
            if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
                raise PodcastAudioError("eSpeak NG 未返回支持的语音格式。")
            return wav.readframes(wav.getnframes()), wav.getframerate()
    except (OSError, EOFError, wave.Error):
        raise PodcastAudioError(failed) from None


def _speech_phrases(text: str, language: str) -> Iterable[tuple[str, int, str]]:
    """Yield text and pause milliseconds, keeping short sentences intact."""
    for paragraph in text.splitlines():
        sentences = [part.strip() for part in _SENTENCE_BREAK.split(paragraph) if part.strip()]
        for index, sentence in enumerate(sentences):
            if not any(character.isalnum() for character in sentence):
                continue
            spoken_language = language
            if language == "bilingual":
                han_count = len(_HAN.findall(sentence))
                latin_words = re.findall(r"[A-Za-z]+", sentence)
                spoken_language = "zh" if han_count and han_count >= len(latin_words) else "en"
            while len(sentence) > _MAX_PHRASE_CHARS:
                window = sentence[:_MAX_PHRASE_CHARS + 1]
                clauses = [match.start() for match in _CLAUSE_BREAK.finditer(window)]
                words = [match.start() for match in _WORD_BREAK.finditer(window)]
                cut = max((pos for pos in clauses if 60 <= pos <= _MAX_PHRASE_CHARS), default=0)
                if not cut:
                    cut = max(words, default=0)
                if not cut:
                    raise PodcastAudioError("讲稿包含无法分段的超长词语，请缩短后重试。")
                yield sentence[:cut].strip(), 90, spoken_language
                sentence = sentence[cut:].strip()
            if sentence:
                yield sentence, 280 if index == len(sentences) - 1 else 140, spoken_language


def _kokoro(text: str, language: str) -> tuple[Iterable[bytes], int] | None:
    directory = Path(os.environ.get(_MODEL_DIR_ENV) or _DEFAULT_MODEL_DIR).expanduser().resolve()
    if not (
        all((directory / file).is_file() for file in _MODEL_FILES)
        and (directory / "espeak-ng-data").is_dir()
    ):
        return None

    try:
        import numpy as np
        import sherpa_onnx as sherpa
    except ImportError:
        return None

    try:
        config = sherpa.OfflineTtsConfig(
            model=sherpa.OfflineTtsModelConfig(
                kokoro=sherpa.OfflineTtsKokoroModelConfig(
                    model=str(directory / "model.int8.onnx"),
                    voices=str(directory / "voices.bin"),
                    tokens=str(directory / "tokens.txt"),
                    lexicon=",".join(str(directory / name) for name in _LEXICONS),
                    data_dir=str(directory / "espeak-ng-data"),
                ),
                num_threads=2,
                provider="cpu",
            ),
            rule_fsts=",".join(str(directory / name) for name in _RULES),
            max_num_sentences=1,
        )
        if not config.validate():
            raise ValueError("Invalid Kokoro configuration")
        tts = sherpa.OfflineTts(config)
        rate = tts.sample_rate
        if rate <= 0 or max(_KOKORO_SPEAKERS.values()) >= tts.num_speakers:
            raise ValueError("Invalid Kokoro audio format or voice")
    except (RuntimeError, ValueError, OSError):
        raise PodcastAudioError("本地 Kokoro 语音模型无法加载，请检查模型文件后重试。") from None

    def chunks() -> Iterable[bytes]:
        previous_pause = 0
        for phrase, pause, spoken_language in _speech_phrases(text, language):
            try:
                generated = tts.generate(phrase, sid=_KOKORO_SPEAKERS[spoken_language], speed=1.0)
                samples = np.asarray(generated.samples)
                if (
                    generated.sample_rate != rate or samples.ndim != 1 or not samples.size
                    or samples.size * 2 > _MAX_WAV_BYTES or not np.isfinite(samples).all()
                ):
                    raise ValueError("Invalid Kokoro waveform")
                pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
                if not pcm.strip(b"\x00"):
                    raise ValueError("Silent Kokoro waveform")
            except (RuntimeError, ValueError, OSError):
                raise PodcastAudioError("本地 Kokoro 未能生成可播放的音频，请检查讲稿后重试。") from None
            if previous_pause:
                yield bytes(rate * previous_pause // 1000 * 2)
            yield pcm
            previous_pause = pause

    return chunks(), rate


def _validate_request(text: str, language: str) -> bytes:
    if language not in _LANGUAGES:
        raise PodcastAudioError("语音语言必须为 zh、en 或 bilingual。")
    try:
        request = text.encode("utf-8")
    except UnicodeError:
        raise PodcastAudioError("讲稿包含无法编码的字符。") from None
    if len(request) > _MAX_REQUEST_BYTES:
        raise PodcastAudioError("讲稿超过语音合成的输入大小限制。")
    if not any(character.isalnum() for character in text):
        raise PodcastAudioError("讲稿没有可朗读的文字。")
    return request


def _system_voice(text: str, language: str) -> tuple[Iterable[bytes], int]:
    engine = _sapi_pcm if sys.platform == "win32" else _espeak_pcm
    invalid_output = "本地语音引擎没有生成可播放的音频，请检查语音设置后重试。"

    def synthesize(phrase: str, spoken_language: str) -> tuple[bytes, int]:
        pcm, rate = engine(phrase, spoken_language)
        if rate <= 0 or len(pcm) % 2 or not pcm.strip(b"\x00"):
            raise PodcastAudioError(invalid_output)
        return pcm, rate

    if language != "bilingual":
        pcm, rate = synthesize(text, language)
        return (pcm,), rate

    phrases = iter(_speech_phrases(text, language))
    phrase, first_pause, spoken_language = next(phrases)
    first_pcm, rate = synthesize(phrase, spoken_language)

    def chunks() -> Iterable[bytes]:
        yield first_pcm
        previous_pause = first_pause
        for phrase, pause, spoken_language in phrases:
            pcm, phrase_rate = synthesize(phrase, spoken_language)
            if phrase_rate != rate:
                raise PodcastAudioError(invalid_output)
            yield bytes(rate * previous_pause // 1000 * 2)
            yield pcm
            previous_pause = pause

    return chunks(), rate


def render_mp3(text: str, language: str = "zh") -> tuple[bytes, float]:
    _validate_request(text, language)
    try:
        import lameenc
    except ImportError:
        raise PodcastAudioError("缺少离线 MP3 编码器 lameenc，请安装后重试。") from None

    voice = _kokoro(text, language)
    chunks, rate = voice if voice is not None else _system_voice(text, language)

    audio = bytearray()
    pcm_bytes = 0
    try:
        encoder = lameenc.Encoder()
        encoder.set_in_sample_rate(rate)
        encoder.set_channels(1)
        encoder.set_bit_rate(_MP3_BIT_RATE)
        encoder.set_quality(2)
        for pcm in chunks:
            pcm_bytes += len(pcm)
            if pcm_bytes > _MAX_WAV_BYTES:
                raise PodcastAudioError(_OUTPUT_TOO_LARGE)
            audio.extend(encoder.encode(pcm))
        if pcm_bytes:
            audio.extend(encoder.flush())
    except PodcastAudioError:
        raise
    except (RuntimeError, ValueError, OSError):
        raise PodcastAudioError("MP3 编码失败，请重试。") from None
    if len(audio) > _MAX_MP3_BYTES:
        raise PodcastAudioError(_OUTPUT_TOO_LARGE)
    if not _has_mp3_frame(audio):
        raise PodcastAudioError("MP3 编码器没有返回可播放的音频，请重试。")
    return bytes(audio), _mp3_duration(audio)


def _has_mp3_frame(audio: bytes | bytearray) -> bool:
    return (
        len(audio) >= 192
        and audio[0] == 0xFF
        and audio[1] & 0xE6 == 0xE2  # sync and Layer III
        and audio[1] & 0x18 != 0x08  # reserved MPEG version
        and audio[2] & 0xF0 not in (0, 0xF0)  # valid bitrate
        and audio[2] & 0x0C != 0x0C  # valid sample rate
    )


def _mp3_duration(audio: bytes | bytearray) -> float:
    # The constant-rate MP3 duration includes padding added during encoding.
    return len(audio) * 8 / (_MP3_BIT_RATE * 1000)


def _worker_main(language: str) -> None:
    request = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
    try:
        if len(request) > _MAX_REQUEST_BYTES:
            raise PodcastAudioError("讲稿超过语音合成的输入大小限制。")
        try:
            text = request.decode("utf-8")
        except UnicodeError:
            raise PodcastAudioError("讲稿包含无法编码的字符。") from None
        audio, _ = render_mp3(text, language)
    except PodcastAudioError as exc:
        sys.stdout.buffer.write(_ERROR_REPLY + str(exc).encode("utf-8"))
    else:
        sys.stdout.buffer.write(_AUDIO_REPLY)
        sys.stdout.buffer.write(audio)


async def _stop_worker(startup: asyncio.Task[asyncio.subprocess.Process]) -> None:
    try:
        process = await startup
    except OSError:
        return
    with suppress(ProcessLookupError):
        if sys.platform == "win32":
            process.kill()
        else:
            # eSpeak is a descendant in this helper's private session.
            os.killpg(process.pid, signal.SIGKILL)
    process.stdin.close()
    try:
        # Draining the closed pipe lets asyncio finish reaping a backpressured child.
        with suppress(BrokenPipeError, ConnectionResetError):
            while await process.stdout.read(_PIPE_CHUNK_SIZE):
                pass
    finally:
        await process.wait()


async def _reap_worker(startup: asyncio.Task[asyncio.subprocess.Process]) -> None:
    cleanup = asyncio.create_task(_stop_worker(startup))
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        while not cleanup.done():
            with suppress(asyncio.CancelledError):
                await asyncio.shield(cleanup)
        cleanup.result()
        raise


async def render_mp3_in_worker(text: str, language: str = "zh") -> tuple[bytes, float]:
    request = _validate_request(text, language)
    # CPU budgets use character count, with extra time for Han characters, which
    # represent more spoken audio per character than English prose.
    han_count = len(_HAN.findall(text))
    estimate = 0.4 * (len(text) - han_count) + 1.65 * han_count
    deadline = min(_MAX_SYNTHESIS_TIMEOUT, _SYNTHESIS_TIMEOUT + max(0, estimate - 60))
    options = (
        {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    )
    startup = asyncio.create_task(asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        str(Path(__file__).resolve()),
        language,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        **options,
    ))
    exited_cleanly = False
    try:
        async with asyncio.timeout(deadline):
            # Keep ownership of a process whose launch races with cancellation.
            process = await asyncio.shield(startup)
            process.stdin.write(request)
            process.stdin.close()
            try:
                await process.stdout.readexactly(1 + _MAX_MP3_BYTES + 1)
            except asyncio.IncompleteReadError as exc:
                reply = exc.partial
            else:
                raise PodcastAudioError(_OUTPUT_TOO_LARGE)
            exited_cleanly = await process.wait() == 0
    except TimeoutError:
        raise PodcastAudioError("本地语音合成超时，请缩短讲稿后重试。") from None
    except OSError:
        raise PodcastAudioError(_WORKER_FAILED) from None
    finally:
        if not exited_cleanly:
            await _reap_worker(startup)

    if exited_cleanly and reply[:1] == _AUDIO_REPLY and _has_mp3_frame(reply[1:]):
        audio = reply[1:]
        return audio, _mp3_duration(audio)
    if exited_cleanly and reply[:1] == _ERROR_REPLY and 1 < len(reply) <= 1 + _MAX_ERROR_BYTES:
        raise PodcastAudioError(reply[1:].decode("utf-8", errors="replace"))
    raise PodcastAudioError(_WORKER_FAILED)


if __name__ == "__main__":
    _worker_main(sys.argv[1] if len(sys.argv) > 1 else "zh")
