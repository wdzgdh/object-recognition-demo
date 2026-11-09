# -*- coding: utf-8 -*-
"""语音基础设施测试（B3）：用替身顶掉 pyttsx3 / 麦克风 / WebSocket。

这些用例只验证**逻辑**（去重、打断、复用、流式拼句、取消），
不打开音频设备，因此能在任何机器 / CI 上跑。
"""
import json
import threading
import time

import pytest

import asr_teleai
import config
import speech as speech_mod
from speech import ListenHandle, Speech


def wait_until(predicate, timeout=3.0):
    """轮询等待，避免用例里写死 sleep。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------- TTS 替身

class FakeEngine:
    """记录 say() 的内容；gate 用来模拟"这句还没播完"。"""

    def __init__(self, gate=None, fail=False):
        self.said = []
        self.stopped = False
        self.gate = gate
        self.fail = fail
        self.rate = None

    def setProperty(self, key, value):
        if key == "rate":
            self.rate = value

    def say(self, text):
        if self.fail:
            raise RuntimeError("boom")
        self.said.append(text)

    def runAndWait(self):
        if self.gate is not None:
            self.gate.wait(5.0)

    def stop(self):
        self.stopped = True


def make_speech(gate=None, fail_times=0, enable_tts=True):
    """返回 (Speech, engines)；engines 是工厂造出来的实例列表。"""
    engines = []
    state = {"fail": fail_times}

    def factory():
        fail = state["fail"] > 0
        if fail:
            state["fail"] -= 1
        engine = FakeEngine(gate=gate, fail=fail)
        engines.append(engine)
        return engine

    return Speech(tts_factory=factory, enable_tts=enable_tts), engines


# ---------------------------------------------------------------- TTS 行为

def test_tts_reuses_single_engine(monkeypatch):
    """复用：连续播两句只 init 一次（这是 B3 的效率目标）。"""
    monkeypatch.setattr(config, "TTS_REUSE_ENGINE", True)
    sp, engines = make_speech()
    assert sp.speak("第一句") is True
    assert sp.speak("第二句") is True
    assert wait_until(lambda: engines and engines[0].said == ["第一句", "第二句"])
    assert len(engines) == 1
    assert sp.tts_stats["inits"] == 1


def test_tts_new_engine_per_sentence_when_reuse_off(monkeypatch):
    """TTS_REUSE_ENGINE=False 时退回老行为，每句一个 engine。"""
    monkeypatch.setattr(config, "TTS_REUSE_ENGINE", False)
    sp, engines = make_speech()
    sp.speak("甲")
    sp.speak("乙")
    assert wait_until(lambda: len(engines) == 2)
    assert [e.said for e in engines] == [["甲"], ["乙"]]


def test_tts_dedupe_same_pending_text(monkeypatch):
    """同一句话在队列里只排一次。"""
    monkeypatch.setattr(config, "TTS_REUSE_ENGINE", True)
    gate = threading.Event()
    sp, engines = make_speech(gate=gate)
    assert sp.speak("你好") is True
    assert wait_until(lambda: engines and engines[0].said == ["你好"])
    assert sp.speak("你好") is False          # 阻塞中，重复的丢掉
    gate.set()
    assert wait_until(lambda: not sp._tts_pending)


def test_tts_can_speak_same_text_again_after_finished(monkeypatch):
    monkeypatch.setattr(config, "TTS_REUSE_ENGINE", True)
    sp, engines = make_speech()
    sp.speak("你好")
    assert wait_until(lambda: engines and engines[0].said == ["你好"])
    assert wait_until(lambda: not sp._tts_pending)
    assert sp.speak("你好") is True


def test_tts_queue_is_bounded(monkeypatch):
    """排队太多时丢最旧的，保证最新状态能播出来。"""
    monkeypatch.setattr(config, "TTS_REUSE_ENGINE", True)
    monkeypatch.setattr(config, "TTS_QUEUE_MAX", 2)
    gate = threading.Event()
    sp, engines = make_speech(gate=gate)
    sp.speak("正在播")
    assert wait_until(lambda: engines and engines[0].said == ["正在播"])
    for i in range(8):
        sp.speak("排队%d" % i)
    assert sp._tts_queue.qsize() <= 2
    assert len(sp._tts_pending) <= 3          # 队列 + 正在播的那句
    gate.set()


def test_stop_speaking_clears_queue(monkeypatch):
    monkeypatch.setattr(config, "TTS_REUSE_ENGINE", True)
    gate = threading.Event()
    sp, engines = make_speech(gate=gate)
    sp.speak("正在播")
    assert wait_until(lambda: engines and engines[0].said == ["正在播"])
    sp.speak("待播一")
    sp.speak("待播二")
    sp.stop_speaking()
    assert sp._tts_pending == set()
    assert engines[0].stopped is True
    gate.set()
    time.sleep(0.15)
    assert "待播一" not in engines[0].said


def test_interrupt_replaces_current(monkeypatch):
    monkeypatch.setattr(config, "TTS_REUSE_ENGINE", True)
    gate = threading.Event()
    sp, engines = make_speech(gate=gate)
    sp.speak("正在播")
    assert wait_until(lambda: engines and engines[0].said == ["正在播"])
    sp.speak("新消息", interrupt=True)
    assert engines[0].stopped is True
    assert sp._tts_pending == {"新消息"}
    gate.set()
    assert wait_until(lambda: "新消息" in engines[0].said)


def test_tts_error_is_recorded_and_engine_rebuilt(monkeypatch):
    """第一句抛异常 → 记录错误并丢弃 engine，第二句自动重建。"""
    monkeypatch.setattr(config, "TTS_REUSE_ENGINE", True)
    sp, engines = make_speech(fail_times=1)
    sp.speak("会失败")
    assert wait_until(lambda: sp.tts_stats["last_error"])
    assert "boom" in sp.tts_stats["last_error"]
    sp.speak("会成功")
    assert wait_until(lambda: any("会成功" in e.said for e in engines))
    assert len(engines) == 2


def test_tts_disabled_is_noop():
    sp, engines = make_speech(enable_tts=False)
    assert sp.speak("随便") is False
    assert engines == []


def test_speak_ignores_blank():
    sp, _engines = make_speech(enable_tts=False)
    assert sp.speak("") is False
    assert sp.speak("   ") is False
    assert sp.speak(None) is False


# ---------------------------------------------------------------- 聆听句柄

def test_listen_handle_cancel_and_wait():
    h = ListenHandle()
    assert h.active and not h.cancelled
    assert h.wait(0.05) is False
    h.cancel()
    assert h.cancelled
    h._finish("cancelled")
    assert h.wait(0.05) is True
    assert h.active is False
    h.cancel()                       # 幂等，不抛异常
    assert h.reason == "cancelled"


def test_can_stream_false_without_backend():
    sp, _e = make_speech(enable_tts=False)
    if sp._teleai is None:
        assert sp.can_stream() is False


def test_error_messages_cover_new_code():
    sp, _e = make_speech(enable_tts=False)
    assert sp.error_message("cancelled") == "已取消"
    assert sp.error_message("不存在") == sp.ERRORS["unknown"]


# ---------------------------------------------------------------- 契约兼容接口

def _ok_loop(self, handle, on_final, on_level, on_partial, on_error,
             timeout, phrase_limit, vad):
    """替身聆听循环：立刻给出音量、中间结果与最终文本。"""
    if on_level:
        on_level(0.3)
    if on_partial:
        on_partial("水")
    on_final("水杯")
    handle._finish("ok")


def _cancel_loop(self, handle, on_final, on_level, on_partial, on_error,
                 timeout, phrase_limit, vad):
    """替身聆听循环：一直等到被取消。"""
    deadline = time.time() + 3.0
    while not handle.cancelled and time.time() < deadline:
        time.sleep(0.01)
    on_error("cancelled")
    handle._finish("cancelled")


def test_recognize_speech_async_signature_from_contract(monkeypatch):
    """契约里的四个回调参数都要能传、且真的被调用。"""
    sp, _e = make_speech(enable_tts=False)
    monkeypatch.setattr(Speech, "_listen_loop", _ok_loop)

    got, levels, partials = [], [], []
    handle = sp.recognize_speech_async(
        lambda text, err: got.append((text, err)),
        timeout=2.0,
        on_level=levels.append,
        on_partial=partials.append,
    )
    assert handle.wait(2.0) is True
    assert got == [("水杯", None)]
    assert levels == [0.3]
    assert partials == ["水"]
    assert handle.reason == "ok"


def test_cancel_event_stops_listening(monkeypatch):
    sp, _e = make_speech(enable_tts=False)
    monkeypatch.setattr(Speech, "_listen_loop", _cancel_loop)

    got = []
    ev = threading.Event()
    handle = sp.recognize_speech_async(lambda t, e: got.append((t, e)),
                                       cancel_event=ev)
    ev.set()
    assert handle.wait(2.0) is True
    assert got == [(None, "cancelled")]


def test_cancel_event_already_set(monkeypatch):
    sp, _e = make_speech(enable_tts=False)
    monkeypatch.setattr(Speech, "_listen_loop", _cancel_loop)
    ev = threading.Event()
    ev.set()
    handle = sp.recognize_speech_async(lambda t, e: None, cancel_event=ev)
    assert handle.cancelled
    assert handle.wait(2.0) is True


def test_cancel_method_is_safe_without_session():
    sp, _e = make_speech(enable_tts=False)
    sp.cancel()          # 没有在聆听，不能抛异常


def test_speak_result_wording(monkeypatch):
    monkeypatch.setattr(config, "TTS_REUSE_ENGINE", True)
    sp, engines = make_speech()
    assert sp.speak_result("水杯", False) is True
    assert sp.speak_result(None, True) is True
    assert wait_until(lambda: engines and len(engines[0].said) == 2)
    assert engines[0].said == ["这是水杯", "我不认识这个东西"]


# ---------------------------------------------------------------- 天翼流式

class FakeTimeout(Exception):
    """名字里带 Timeout，被 TeleAIStream.poll() 当成"暂时没消息"。"""


class FakeWS:
    def __init__(self, inbox):
        self.inbox = list(inbox)
        self.sent = []
        self.closed = False

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self):
        if self.inbox:
            return json.dumps(self.inbox.pop(0), ensure_ascii=False)
        raise FakeTimeout("no message")

    def settimeout(self, t):
        self.timeout = t

    def close(self):
        self.closed = True


class FakeClient:
    apikey = "test-key"
    app_id = ""
    hotwords = []
    sample_rate = 16000
    timeout = 2.0

    def __init__(self, ws):
        self.ws = ws

    def _connect(self):
        return self.ws

    def _send(self, ws, obj):
        ws.send(json.dumps(obj, ensure_ascii=False))

    @staticmethod
    def _parse(raw):
        try:
            return json.loads(raw)
        except Exception:
            return {}


@pytest.fixture
def fake_websocket(monkeypatch):
    """让 asr_teleai 认为 websocket-client 已安装。"""
    monkeypatch.setattr(asr_teleai, "websocket", object())


def test_stream_partial_and_final(fake_websocket):
    inbox = [
        {"res_status": 0},                                              # 就绪
        {"res_status": 3, "data": {"results": [{"text": "打开"}]}},      # 中间结果
        {"res_status": 4, "data": {"results": [{"text": "灯"}]}},        # 最终
    ]
    ws = FakeWS(inbox)
    partials = []
    st = asr_teleai.TeleAIStream(FakeClient(ws), on_partial=partials.append)

    assert st.start() is True
    assert st.active is True
    assert ws.sent[0]["rec_status"] == 0

    assert st.push(b"\x00" * asr_teleai.CHUNK_BYTES) == 1
    assert ws.sent[1]["rec_status"] == 1
    assert partials == ["打开"]                       # 中间结果回调出去了

    text, err = st.finish()
    assert err is None
    assert text == "打开灯"
    assert partials[-1] == "打开灯"
    assert ws.sent[-1]["rec_status"] == 2
    assert ws.closed is True


def test_stream_buffers_partial_chunk(fake_websocket):
    """不足 200ms 的音频先攒着，不硬发（否则触发服务端限速错误）。"""
    ws = FakeWS([{"res_status": 0}])
    st = asr_teleai.TeleAIStream(FakeClient(ws))
    st.start()
    assert st.push(b"\x00" * 100) == 0
    assert [m["rec_status"] for m in ws.sent] == [0]
    # 攒够一块就发
    assert st.push(b"\x00" * (asr_teleai.CHUNK_BYTES - 100)) == 1


def test_stream_cancel_keeps_partial(fake_websocket):
    inbox = [{"res_status": 0},
             {"res_status": 3, "data": {"results": [{"text": "水"}]}}]
    ws = FakeWS(inbox)
    st = asr_teleai.TeleAIStream(FakeClient(ws))
    st.start()
    st.push(b"\x00" * asr_teleai.CHUNK_BYTES)
    text = st.cancel()
    assert text == "水"                 # 取消也保留已经听到的部分
    assert st.active is False
    assert ws.closed is True
    assert st.finish() == ("水", None)  # 已经关掉了，直接返回部分结果


def test_stream_start_without_key_raises(fake_websocket):
    client = FakeClient(FakeWS([{"res_status": 0}]))
    client.apikey = ""
    st = asr_teleai.TeleAIStream(client)
    with pytest.raises(RuntimeError):
        st.start()


def test_stream_duplicate_segments_not_repeated(fake_websocket):
    inbox = [
        {"res_status": 0},
        {"res_status": 3, "data": {"results": [{"text": "水杯"}]}},
        {"res_status": 3, "data": {"results": [{"text": "水杯"}]}},   # 重复
        {"res_status": 4, "data": {"results": []}},
    ]
    ws = FakeWS(inbox)
    st = asr_teleai.TeleAIStream(FakeClient(ws))
    st.start()
    st.push(b"\x00" * asr_teleai.CHUNK_BYTES)
    st.push(b"\x00" * asr_teleai.CHUNK_BYTES)
    text, err = st.finish()
    assert err is None
    assert text == "水杯"               # 不会变成 "水杯水杯"
