"""Offline Mandarin engines. Text and audio buffers stay in memory."""
from __future__ import annotations

import asyncio
import io
import os
import shutil
import signal
import subprocess
import sys
import wave
from contextlib import suppress
from pathlib import Path

_MP3_BIT_RATE = 64
_SAPI_PCM_RATE = 22050
_SAPI_PCM_FORMAT = 22  # SAFT22kHz16BitMono
_SAPI_PLAIN_TEXT = 16  # SVSFIsNotXML
_MANDARIN_LOCALES = ("804", "1004", "404")  # zh-CN, zh-SG, zh-TW
_SYNTHESIS_TIMEOUT = 120
_MAX_REQUEST_BYTES = 48_000
# Match the episode job's MP3 storage limit. Allow the larger uncompressed WAV.
_MAX_MP3_BYTES = 64 * 1024 * 1024
_MAX_WAV_BYTES = 384 * 1024 * 1024
_PIPE_CHUNK_SIZE = 64 * 1024
_MAX_ERROR_BYTES = 1024
_AUDIO_REPLY = b"\x00"
_ERROR_REPLY = b"\x01"
_WORKER_FAILED = "本地语音合成进程运行失败，请重试。"
_ESPEAK_FAILED = "eSpeak NG 普通话语音合成失败，请检查普通话语音后重试。"
_OUTPUT_TOO_LARGE = "本地语音合成的音频超过大小限制。"


class PodcastAudioError(ValueError):
    """A safe message the episode job can expose to the user."""


def _sapi_pcm(text: str) -> tuple[bytes, int]:
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
                languages = {value.strip().lower().lstrip("0") for value in token.GetAttribute("Language").split(";")}
                matches = [locale for locale in _MANDARIN_LOCALES if locale in languages]
                if matches:
                    selected.append((_MANDARIN_LOCALES.index(matches[0]), token))
            if not selected:
                raise PodcastAudioError("Windows 没有可用的普通话 SAPI 语音，请安装普通话桌面语音后重试。")

            voice.Voice = min(selected, key=lambda item: item[0])[1]
            stream = Dispatch("SAPI.SpMemoryStream")
            stream.Format.Type = _SAPI_PCM_FORMAT
            voice.AudioOutputStream = stream
            voice.Speak(text, _SAPI_PLAIN_TEXT)
            return bytes(stream.GetData()), _SAPI_PCM_RATE
        finally:
            pythoncom.CoUninitialize()
    except pythoncom.com_error:
        raise PodcastAudioError("Windows 普通话语音合成失败，请检查桌面语音安装后重试。") from None


def _espeak_pcm(text: str) -> tuple[bytes, int]:
    engine = shutil.which("espeak-ng")
    if not engine:
        raise PodcastAudioError("找不到离线普通话语音引擎，请安装带普通话语音的 eSpeak NG。")
    try:
        waveform = io.BytesIO()
        with subprocess.Popen(
            [engine, "--stdout", "-v", "cmn", "-b", "1", "--stdin"],
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
                raise PodcastAudioError(_ESPEAK_FAILED)
        waveform.seek(0)
        with wave.open(waveform) as wav:
            if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
                raise PodcastAudioError("eSpeak NG 未返回支持的语音格式。")
            return wav.readframes(wav.getnframes()), wav.getframerate()
    except (OSError, EOFError, wave.Error):
        raise PodcastAudioError(_ESPEAK_FAILED) from None


def render_mp3(text: str) -> tuple[bytes, float]:
    try:
        import lameenc
    except ImportError:
        raise PodcastAudioError("缺少离线 MP3 编码器 lameenc，请安装后重试。") from None

    pcm, rate = _sapi_pcm(text) if sys.platform == "win32" else _espeak_pcm(text)
    if rate <= 0 or len(pcm) % 2 or not pcm.strip(b"\x00"):
        raise PodcastAudioError("本地语音引擎没有生成可播放的音频，请检查语音设置后重试。")
    try:
        encoder = lameenc.Encoder()
        encoder.set_in_sample_rate(rate)
        encoder.set_channels(1)
        encoder.set_bit_rate(_MP3_BIT_RATE)
        encoder.set_quality(2)
        audio = bytes(encoder.encode(pcm) + encoder.flush())
    except (RuntimeError, ValueError, OSError):
        raise PodcastAudioError("MP3 编码失败，请重试。") from None
    if not audio:
        raise PodcastAudioError("MP3 编码器没有返回音频，请重试。")
    return audio, _mp3_duration(audio)


def _mp3_duration(audio: bytes) -> float:
    # The constant-rate MP3 duration includes padding added during encoding.
    return len(audio) * 8 / (_MP3_BIT_RATE * 1000)


def _worker_main() -> None:
    request = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
    try:
        if len(request) > _MAX_REQUEST_BYTES:
            raise PodcastAudioError("讲稿超过语音合成的输入大小限制。")
        audio, _ = render_mp3(request.decode("utf-8"))
        if len(audio) > _MAX_MP3_BYTES:
            raise PodcastAudioError(_OUTPUT_TOO_LARGE)
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


async def render_mp3_in_worker(text: str) -> tuple[bytes, float]:
    try:
        request = text.encode("utf-8")
    except UnicodeError:
        raise PodcastAudioError("讲稿包含无法编码的字符。") from None
    if len(request) > _MAX_REQUEST_BYTES:
        raise PodcastAudioError("讲稿超过语音合成的输入大小限制。")

    options = (
        {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    )
    startup = asyncio.create_task(asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        str(Path(__file__).resolve()),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        **options,
    ))
    exited_cleanly = False
    try:
        async with asyncio.timeout(_SYNTHESIS_TIMEOUT):
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

    if exited_cleanly and reply[:1] == _AUDIO_REPLY and len(reply) > 1:
        audio = reply[1:]
        return audio, _mp3_duration(audio)
    if exited_cleanly and reply[:1] == _ERROR_REPLY and 1 < len(reply) <= 1 + _MAX_ERROR_BYTES:
        raise PodcastAudioError(reply[1:].decode("utf-8", errors="replace"))
    raise PodcastAudioError(_WORKER_FAILED)


if __name__ == "__main__":
    _worker_main()
