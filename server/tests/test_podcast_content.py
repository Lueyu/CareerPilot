import asyncio
import io
import json
import math
import os
import struct
import sys
import wave
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.config import Settings
from app.services import podcast_content, podcast_voice
from app.services.podcast_content import PodcastAudioError, PodcastScriptError, make_scripts, synthesize_mp3

_SOURCE_URL = "https://docs.stripe.com/api/idempotent_requests"
_QUESTION = {
    "title": "补券服务超时怎么重试",
    "answer": "补偿单号跨重试保持不变，发券侧通过唯一约束保证每个单号至多执行一次。",
    "topic": "可靠性",
    "series": False,
    "source": "公开素材 " + _SOURCE_URL,
}


def episode(**changes):
    return {
        "title": "补券请求超时了，还要再点一次吗？",
        "opening": "假设你在一家电商公司帮客服补发优惠券。终于点下发送，系统却说超时。现在你还敢再点一次吗？",
        "explanation": (
            "先看两件事。请求可能根本没到，也可能券发完了，只是确认消息丢了。你看见的是同一个超时，顾客手里却可能已经多了一张券。"
            "所以给每笔补偿一个固定单号，超时拿原号查。只有查清可以重试，才沿用原号再发。两名客服同时点下去时，"
            "唯一约束让发券端只处理一次。这会多一次查询，也得保存结果。做支付的 Stripe 在公开文档里介绍过同一个键的重试行为。"
        ),
        "interview_answer": (
            "面试里我会这么答。我会把超时当成结果待确认，先查原单，再按原号有限重试。发券端保证单号唯一。"
            "这会增加一次查询，但能避免把同一张券发两次。"
        ),
        "source_urls": [_SOURCE_URL],
        **changes,
    }


def completion(*episodes):
    return {"content": json.dumps({"episodes": episodes}, ensure_ascii=False)}


@pytest.fixture
def configured(monkeypatch):
    env = Settings(
        _env_file=None,
        llm_provider="existing",
        llm_base_url="https://existing.test/v1",
        llm_api_key="environment-secret",
        llm_model="global-model",
    )
    monkeypatch.setattr(podcast_content, "get_settings", lambda: env)
    adapter = AsyncMock(return_value=completion(episode()))
    monkeypatch.setattr(podcast_content.AgentAdapter, "chat_with", adapter)
    session = SimpleNamespace(get=AsyncMock(return_value=None))
    return session, adapter


@pytest.mark.asyncio
async def test_draft_uses_workspace_model_and_keeps_visible_source_details(configured):
    session, adapter = configured
    session.get.return_value = SimpleNamespace(value={"model": "workspace-model"})
    markup = (
        '<p>补券需要固定单号。</p><script>private-hidden-text</script>'
        '<!-- internal-note --><p>通过 <a href="' + _SOURCE_URL + '">Stripe 公开文档</a>核对。</p>'
    )
    result = await make_scripts(session, 27, **{**_QUESTION, "answer": markup, "source": "题库"})
    config, prompt, request = adapter.await_args.args
    sent = json.loads(request)
    assert session.get.await_args.args[1] == "llm:27"
    assert config["model"] == "workspace-model"
    # Podcast script generation is the dominant streaming path; ask the gateway
    # for the terminal usage event so its tokens are not booked as zero.
    assert config["report_usage"] is True
    assert adapter.await_args.kwargs["json_mode"] is True
    assert all(phrase in prompt for phrase in ("是假设", "是谁公开", "只靠音频", "第一人称", "从中间开始听"))
    assert "补券需要固定单号。\n\n通过 Stripe 公开文档核对。" == sent["answer"]
    assert sent["allowed_source_urls"] == [_SOURCE_URL]
    assert sent["title"] == _QUESTION["title"]
    assert result == [{
        "title": episode()["title"],
        "script": "\n\n".join(episode()[key] for key in ("opening", "explanation", "interview_answer"))
        + "\n\n参考资料（不朗读）\n" + _SOURCE_URL,
    }]


_ENGLISH_ANSWER = (
    "I would use a stable operation key for each coupon. On timeout, I would look up the result with the original "
    "key before retrying. A unique constraint prevents concurrent requests from issuing two coupons. That costs "
    "an extra lookup and means the service must keep the result for the whole retry window."
)


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "bilingual"])
async def test_language_selects_prompt_validation_and_synthesis(configured, monkeypatch, language):
    session, adapter = configured
    if language == "en":
        draft = episode(
            title="Retrying a coupon request",
            opening="Imagine your support team issues a replacement coupon and times out. Would you send it again?",
            explanation=_ENGLISH_ANSWER + " The customer could otherwise receive the coupon twice.",
            interview_answer=_ENGLISH_ANSWER,
        )
    else:
        draft = episode(
            opening=episode()["opening"].replace("补发优惠券", "通过 Coupon API 补发优惠券"),
            interview_answer="这段面试回答可以这样说。 " + _ENGLISH_ANSWER,
        )
    adapter.return_value = completion(draft)
    result, = await make_scripts(session, 27, **_QUESTION, language=language)
    _, prompt, request = adapter.await_args.args
    assert json.loads(request)["language"] == language
    assert ("conversational English" if language == "en" else "四十个英文单词") in prompt
    assert draft["interview_answer"] in result["script"]
    worker = AsyncMock(return_value=(b"audio", 1.0))
    monkeypatch.setattr(podcast_content, "render_mp3_in_worker", worker)
    assert await synthesize_mp3(result["script"], language) == (b"audio", 1.0)
    spoken = worker.await_args.args[0]
    assert spoken.startswith(draft["opening"])
    assert _SOURCE_URL not in spoken
    worker.assert_awaited_once_with(spoken, language=language)

    draft["interview_answer"] = (
        _ENGLISH_ANSWER + "中文" if language == "en" else "会用 idempotency key，避免 duplicate side effects。"
    )
    adapter.return_value = completion(draft)
    with pytest.raises(PodcastScriptError, match="语言不符"):
        await make_scripts(session, 27, **_QUESTION, language=language)


_GERMAN_ANSWER = (
    "Ich verwende bei jeder Anfrage denselben festen Wert und speichere das Ergebnis in der Datenbank. "
    "Wenn eine Antwort verloren geht, lese ich den alten Wert aus der Datenbank und sende ihn erneut. "
    "Dadurch bleibt die Anzahl der Buchungen auch nach einem Fehler immer gleich."
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("language", "changes"), [
    ("en", {
        "title": "Wiederholung bei einem Fehler",
        "opening": "Was machen Sie mit einer Anfrage wenn die Antwort auf dem Weg zum Kunden verloren geht?",
        "explanation": _GERMAN_ANSWER * 2,
        "interview_answer": _GERMAN_ANSWER,
    }),
    ("bilingual", {"interview_answer": "面试中可以这样回答。 " + _GERMAN_ANSWER}),
    ("bilingual", {
        "explanation": "如果请求超时了，我们需要先检查数据库里有没有执行记录，再决定能否重试。" * 4
        + " " + _ENGLISH_ANSWER * 6,
        "interview_answer": _ENGLISH_ANSWER,
    }),
])
async def test_draft_rejects_wrong_language_in_spoken_prose(configured, language, changes):
    session, adapter = configured
    adapter.return_value = completion(episode(**changes))
    with pytest.raises(PodcastScriptError, match="语言不符"):
        await make_scripts(session, 27, **_QUESTION, language=language)


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["zh", "en", "bilingual"])
async def test_draft_accepts_short_questions_and_technical_terms(configured, language):
    session, adapter = configured
    if language == "en":
        draft = episode(
            title="Redis + HTTP 429",
            opening="Retry the API call?",
            explanation=_ENGLISH_ANSWER + " The customer could otherwise receive the same coupon twice.",
            interview_answer=_ENGLISH_ANSWER,
        )
    else:
        explanation = (
            episode()["explanation"]
            .replace("同一个超时", "同一个 API timeout")
            .replace("固定单号", "固定的 idempotency key")
            .replace("一次查询", "一次 Redis lookup")
        )
        draft = episode(opening="HTTP 429 要重试吗？", explanation=explanation)
        if language == "bilingual":
            draft["interview_answer"] = "面试可以这样说。 " + _ENGLISH_ANSWER + " 这就是取舍。"
    adapter.return_value = completion(draft)
    result, = await make_scripts(session, 27, **_QUESTION, language=language)
    assert result["script"].startswith(draft["opening"])


@pytest.mark.asyncio
@pytest.mark.parametrize(("language", "script"), [
    ("en", _GERMAN_ANSWER),
    ("bilingual", "请听这段面试回答。 " + _GERMAN_ANSWER),
])
async def test_synthesis_rejects_non_english_prose_before_starting_worker(monkeypatch, language, script):
    worker = AsyncMock()
    monkeypatch.setattr(podcast_content, "render_mp3_in_worker", worker)
    with pytest.raises(PodcastAudioError, match="所选语言"):
        await synthesize_mp3(script, language)
    worker.assert_not_awaited()


@pytest.mark.asyncio
async def test_series_preserves_plain_answer_and_episode_order(configured):
    session, adapter = configured
    topics = [
        episode(),
        episode(
            title="为什么查询不能解决并发",
            explanation=(
                "把镜头切回客服台。两个客服几乎同时收到同一张工单，打开之后都读到还没有发券。"
                "先查询这个动作并不会让后续写入排队，所以两个人依然可以一起提交。"
                "把唯一约束落到负责发券的服务里，才能保证只有一笔真正生效。还要决定唯一约束和发券记录是不是同一个事务能覆盖。"
                "如果服务之间无法共享事务，就设计明确的处理中状态并做好对账。"
            ),
            source_urls=[],
        ),
    ]
    adapter.return_value = completion(*topics)
    answer = "Look up List<Compensation> for orders where count > 1.\n" + _QUESTION["answer"]
    result = await make_scripts(session, 27, **{**_QUESTION, "series": True, "answer": answer})
    assert json.loads(adapter.await_args.args[2])["answer"] == answer
    assert [part["title"] for part in result] == [part["title"] for part in topics]
    assert [call.args[1] for call in session.get.await_args_list] == ["llm:27", "llm"]


@pytest.mark.asyncio
@pytest.mark.parametrize("response, series, reason", [
    (None, False, "已配置的模型"),
    ({"content": "private-provider-reply"}, False, "完整"),
    (completion(), False, "缺少关键细节"),
    (completion(episode(), episode(title="第二集")), False, "多集"),
    (completion(episode(), episode(title="第二集")), True, "重复"),
    (completion(episode(source_urls=["https://invented.invalid/"])), False, "引用与素材不符"),
    (completion(episode(explanation="这里谈幂等，大家回去看图。")), False, "讲解"),
])
async def test_failed_generation_does_not_return_a_canned_draft(configured, response, series, reason):
    session, adapter = configured
    adapter.return_value = response
    with pytest.raises(PodcastScriptError, match=reason) as error:
        await make_scripts(session, 2, **{**_QUESTION, "series": series})
    assert "private-provider-reply" not in str(error.value)


@pytest.mark.asyncio
async def test_model_credentials_are_not_reused_for_a_different_endpoint(configured):
    session, adapter = configured
    session.get.return_value = SimpleNamespace(value={"base_url": "https://other.test/v1"})
    with pytest.raises(PodcastScriptError, match="配置可用的语言模型"):
        await make_scripts(session, 2, **_QUESTION)
    adapter.assert_not_awaited()


@pytest.mark.asyncio
async def test_hides_non_answer_markup_before_contacting_the_model(configured):
    session, adapter = configured
    with pytest.raises(PodcastScriptError, match="参考答案为空"):
        await make_scripts(session, 2, **{**_QUESTION, "answer": "<!-- private --> <style>h1 {color:blue}</style>"})
    adapter.assert_not_awaited()


def test_espeak_uses_explicit_mandarin_and_stdin_and_returns_mp3(monkeypatch):
    monkeypatch.setattr(podcast_voice, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(podcast_voice.shutil, "which", lambda name: f"/usr/bin/{name}")
    spoken = "请先查补偿单，然后决定要不要重试。"
    pcm_wav = io.BytesIO()
    with wave.open(pcm_wav, "wb") as wav:
        wav.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
        wav.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 10))) for i in range(22050)))

    class SpeechInput(io.BytesIO):
        def close(self):
            if not self.closed:
                assert self.getvalue() == spoken.encode("utf-8")
            super().close()

    def start(args, **kwargs):
        assert args == ["/usr/bin/espeak-ng", "--stdout", "-v", "cmn", "-b", "1", "--stdin"]
        assert kwargs["stdin"] == podcast_voice.subprocess.PIPE
        assert not kwargs.get("shell")
        return nullcontext(SimpleNamespace(stdin=SpeechInput(), stdout=pcm_wav, wait=lambda: 0))

    pcm_wav.seek(0)
    monkeypatch.setattr(podcast_voice.subprocess, "Popen", start)
    audio, seconds = podcast_voice.render_mp3(spoken)
    assert audio[:2] == b"\xff\xf3" and len(audio) > 8000
    assert 1 < seconds < 1.15


def test_unavailable_engine_fails_explicitly(monkeypatch):
    monkeypatch.setattr(podcast_voice, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(podcast_voice.shutil, "which", lambda _name: None)
    with pytest.raises(PodcastAudioError, match="eSpeak NG"):
        podcast_voice.render_mp3("测试离线普通话语音。")


@pytest.mark.asyncio
async def test_bad_synthesis_input_fails_explicitly():
    with pytest.raises(PodcastAudioError, match="有效的中文"):
        await synthesize_mp3("No Mandarin content")


@pytest.mark.asyncio
async def test_synthesis_sends_only_spoken_text_to_worker(monkeypatch):
    render = AsyncMock(return_value=(b"audio", 1.0))
    monkeypatch.setattr(podcast_content, "render_mp3_in_worker", render)
    spoken = "请先查补偿单，然后决定要不要重试。"
    assert await synthesize_mp3(spoken + "\n\n参考资料（不朗读）\n" + _SOURCE_URL) == (b"audio", 1.0)
    render.assert_awaited_once_with(spoken)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32" or os.getenv("CAREERPILOT_TEST_PODCAST_SAPI") != "1",
                    reason="requires an installed Mandarin SAPI voice")
async def test_installed_mandarin_sapi_voice_produces_playable_mp3():
    audio, seconds = await synthesize_mp3("先别急着点第二次。先查补偿单的结果，再用同一个单号决定是否重试。")
    assert audio[:2] == b"\xff\xf3" and len(audio) > 10000
    assert seconds > 2


@pytest.mark.skipif(sys.platform != "win32", reason="exercises the Windows SAPI voice boundary")
def test_windows_never_falls_back_to_the_english_voice(monkeypatch):
    from win32com.client import dynamic

    english_voice = SimpleNamespace(GetAttribute=lambda _: "409")
    cantonese_voice = SimpleNamespace(GetAttribute=lambda _: "0C04")
    fake_sapi = SimpleNamespace(GetVoices=lambda: [english_voice, cantonese_voice])
    monkeypatch.setattr(dynamic, "Dispatch", lambda _: fake_sapi)
    with pytest.raises(PodcastAudioError, match="没有可用的普通话"):
        podcast_voice.render_mp3("测试离线普通话语音。")

@pytest_asyncio.fixture
async def voice_worker(monkeypatch, request):
    loop = asyncio.get_running_loop()
    started = loop.create_future()
    allow_start = asyncio.Event()
    create_subprocess = asyncio.create_subprocess_exec

    async def start(*args, **kwargs):
        assert "测试离线普通话语音" not in " ".join(args)
        assert kwargs["stderr"] == asyncio.subprocess.DEVNULL
        if sys.platform == "win32":
            assert kwargs["creationflags"] & podcast_voice.subprocess.CREATE_NO_WINDOW
        else:
            assert kwargs["start_new_session"]
        worker_code = getattr(request, "param", "import time; time.sleep(60)")
        process = await create_subprocess(
            sys.executable, "-c", worker_code, **kwargs,
        )
        started.set_result(process)
        await allow_start.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    yield started, allow_start
    if started.done():
        process = started.result()
        if process.returncode is None:
            process.kill()
        await process.wait()


@pytest.mark.asyncio
async def test_synthesis_cancellation_joins_worker_even_during_startup(voice_worker):
    started, allow_start = voice_worker
    synthesis = asyncio.create_task(synthesize_mp3("测试离线普通话语音。"))
    try:
        process = await asyncio.wait_for(asyncio.shield(started), 3)
        synthesis.cancel()
        await asyncio.sleep(0)
        synthesis.cancel()
        await asyncio.sleep(0)
        assert not synthesis.done()
        allow_start.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(synthesis, 3)
        assert process.returncode is not None
    finally:
        allow_start.set()
        if not synthesis.done():
            synthesis.cancel()
        await asyncio.gather(synthesis, return_exceptions=True)


@pytest.mark.asyncio
async def test_synthesis_deadline_kills_and_joins_hung_worker(monkeypatch, voice_worker):
    started, allow_start = voice_worker
    allow_start.set()
    monkeypatch.setattr(podcast_voice, "_SYNTHESIS_TIMEOUT", 0.2)
    with pytest.raises(PodcastAudioError, match="超时"):
        await asyncio.wait_for(synthesize_mp3("测试离线普通话语音。"), 3)
    assert started.result().returncode is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "voice_worker",
    ["import sys, time; sys.stdout.buffer.write(bytes(262144)); sys.stdout.buffer.flush(); time.sleep(60)"],
    indirect=True,
)
async def test_synthesis_joins_worker_that_exceeds_output_limit(monkeypatch, voice_worker):
    started, allow_start = voice_worker
    allow_start.set()
    monkeypatch.setattr(podcast_voice, "_MAX_MP3_BYTES", 1024)
    with pytest.raises(PodcastAudioError, match="超过大小限制"):
        await asyncio.wait_for(synthesize_mp3("测试离线普通话语音。"), 3)
    assert started.result().returncode is not None
