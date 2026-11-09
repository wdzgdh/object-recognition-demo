# -*- coding: utf-8 -*-
"""语音交互：语音识别（STT）+ 语音合成（TTS）。

设计要点（针对 Windows + Qt 环境）：

- TTS：pyttsx3 底层是 SAPI5(COM)，engine 必须在**使用它的那个线程**里创建。
  这里用「单一常驻工作线程 + 队列」：所有播报请求排队，worker 线程内
  初始化 COM。engine **默认复用**（每次 init 要枚举 SAPI 语音，100~300 ms，
  是"点了没反应"的主因）；一旦某次 runAndWait 抛异常就丢弃重来，绝不会
  退化成"只能播报一次"。个别机器复用后第二句不出声，把
  `config.TTS_REUSE_ENGINE` 改成 False 即可回到每句新建。

- STT：speech_recognition 录麦克风 → 存临时 WAV → 交给识别后端。
  后端由 config.STT_BACKEND 决定，且**任何在线后端失败都会自动降级到
  Windows SAPI 中文离线识别**（系统自带 MS-2052-80-DESK），保证演示不中断：
    "teleai" = 天翼 AI 开放平台实时语音识别（WebSocket 流式）
    "baidu"  = 百度短语音 REST API
    "google" = Google 在线识别（国内通常不可达）
    "sapi"   = 纯离线

- 所有 STT 失败都返回结构化错误原因，不再静默吞异常；回调由 UI 层通过
  Qt 信号切回主线程，避免跨线程操作控件。

B3 新增两种能力（老接口 `recognize_speech()` 保持不动，A 已有的代码不用改）：

- **`listen(...)`：可取消、可实时反馈的流式聆听。**
  自己按块读麦克风（`AUDIO_CHUNK_MS`），因此能：
    ① 提供 `on_level` 实时音量 → UI 画音量条，用户知道"它在听"；
    ② 提供 `on_partial` 中间结果 → 边说边出字；
    ③ 返回 `ListenHandle`，`cancel()` 立刻停（最坏 1 个块 ≈ 100 ms）；
    ④ 有流式后端时边录边推，说完即可出结果，不必等整段上传。
  没有流式后端（百度 REST / 离线 SAPI）时自动退化成"录完再识别"，
  接口不变，只是没有 `on_partial`。

- **TTS 去重与打断。** 同一句话在队列里只排一次；`interrupt=True` 会清空队列
  并让正在播的那句闭嘴，避免连续识别到同一个物体时"你好你好你好"。
"""
import os
import queue
import tempfile
import threading
import time

import config
from utils.audio import is_speech, pcm_rms, pcm_to_wav_bytes


class _SapiRecoEvents:
    """SAPI 识别事件接收器（由 win32com WithEvents 使用）。"""

    def __init__(self):
        self.text = None
        self.finished = False

    def OnRecognition(self, StreamNumber, StreamPosition, RecognitionType, Result):
        try:
            import win32com.client
            res = win32com.client.Dispatch(Result)
            self.text = res.PhraseInfo.GetText()
        except Exception:
            self.text = None

    def OnEndStream(self, StreamNumber, StreamPosition, StreamReleased):
        self.finished = True


class ListenHandle:
    """一次聆听会话的句柄：用来取消、等结果、查状态。

    UI 侧典型用法：

        self._handle = self.speech.listen(on_final=..., on_level=...)
        # 用户点"停止"
        self._handle.cancel()

    `cancel()` 是幂等的、线程安全的，可在任意线程调用。
    """

    def __init__(self):
        self._cancel = threading.Event()
        self._done = threading.Event()
        self.reason = None          # "ok" / "cancelled" / 错误码

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self):
        return self._cancel.is_set()

    @property
    def active(self):
        return not self._done.is_set()

    def wait(self, timeout=None):
        """等这次聆听结束（含回调已执行）。返回是否已结束。"""
        return self._done.wait(timeout)

    # ---- 内部 ----
    def _finish(self, reason):
        self.reason = reason
        self._done.set()


def _default_tts_factory():
    """默认的 TTS engine 工厂（延迟导入 pyttsx3，缺依赖时才报错）。"""
    import pyttsx3
    return pyttsx3.init()


class Speech:
    # 错误码 → 用户可读提示
    ERRORS = {
        "no_mic": "未检测到可用麦克风，请检查设备或权限",
        "timeout": "未检测到语音，请靠近麦克风再试",
        "no_result": "没能听清，请说清楚一点（也可直接用键盘输入）",
        "engine": "离线语音引擎不可用，请改用键盘输入",
        "cancelled": "已取消",
        "unknown": "语音识别失败，请改用键盘输入",
    }

    def __init__(self, tts_factory=None, enable_tts=True):
        # ---- TTS：队列 + 常驻 worker + 单 engine 复用 ----
        self._tts_queue = queue.Queue()
        self._tts_pending = set()       # 已入队 + 正在播的文本（去重用）
        self._tts_lock = threading.Lock()
        self._tts_engine = None
        self._tts_factory = tts_factory or _default_tts_factory
        self._tts_inits = 0             # engine 创建次数，测试与排查用
        self._last_tts_error = None
        self._enable_tts = enable_tts
        # 打断用"代际"：stop_speaking() 让代号 +1，worker 丢掉旧代号的句子。
        # 这样"打断"只干掉打断之前的内容，不会误伤紧接着的新消息。
        self._tts_gen = 0
        if enable_tts:
            self._tts_worker = threading.Thread(
                target=self._tts_loop, daemon=True, name="tts-worker")
            self._tts_worker.start()

        # ---- 在线 ASR（可选） ----
        self._baidu = None
        self._teleai = None
        self._online_backend = getattr(config, "STT_BACKEND", "sapi")
        self._last_backend_used = "sapi"
        self._last_handle = None

        if self._online_backend == "teleai":
            try:
                from asr_teleai import TeleAIASR
                app_id, appkey, apikey, hotwords = config.load_teleai_credentials()
                self._teleai = TeleAIASR(
                    app_id=app_id, appkey=appkey, apikey=apikey,
                    hotwords=hotwords,
                    timeout=config.TELEAI_TIMEOUT,
                    sample_rate=config.TELEAI_SAMPLE_RATE,
                )
            except Exception:
                self._teleai = None
        elif self._online_backend == "baidu":
            try:
                from asr_baidu import BaiduASR
                key, secret, hotword = config.load_asr_keys()
                self._baidu = BaiduASR(
                    key, secret,
                    cache_path=config.ASR_TOKEN_CACHE,
                    timeout=config.BAIDU_TIMEOUT,
                    dev_pid=config.BAIDU_DEV_PID,
                    hotword=hotword,
                )
            except Exception:
                self._baidu = None

    def backend_status(self):
        """给 UI 状态栏用的一行后端说明。"""
        if self._online_backend == "teleai":
            if self._teleai is None or not self._teleai.configured:
                return "天翼 ASR 未就绪（缺依赖/密钥），当前使用离线 SAPI"
            return "天翼 ASR 已配置（失败自动降级离线 SAPI）"
        if self._online_backend == "baidu":
            if self._baidu is None or not self._baidu.configured:
                return "百度 ASR 未配置密钥，当前使用离线 SAPI"
            return "百度 ASR 已配置（失败自动降级离线 SAPI）"
        if self._online_backend == "google":
            return "Google ASR（失败自动降级离线 SAPI）"
        return "离线 SAPI 中文识别"

    # ================= TTS =================
    def speak(self, text, dedupe=True, interrupt=False):
        """异步播报文本。

        参数:
            text:      要播报的内容
            dedupe:    True 时，同一句话已经在排队就跳过（避免"你好你好你好"）
            interrupt: True 时先打断正在播的、清空队列，再播这句
        返回:
            bool，是否真的入队（被去重掉返回 False）
        """
        text = (text or "").strip()
        if not text or not self._enable_tts:
            return False

        if interrupt:
            self.stop_speaking()

        with self._tts_lock:
            if dedupe and text in self._tts_pending:
                return False
            # 队列上限：丢最旧的，保证"最新状态"总能播出来
            while self._tts_queue.qsize() >= getattr(config, "TTS_QUEUE_MAX", 4):
                try:
                    old, _gen = self._tts_queue.get_nowait()
                    self._tts_queue.task_done()
                    self._tts_pending.discard(old)
                except queue.Empty:
                    break
            self._tts_pending.add(text)
            gen = self._tts_gen
        self._tts_queue.put((text, gen))
        return True

    def stop_speaking(self):
        """立刻闭嘴：清空待播队列，并让正在播的那句停下。

        `_tts_pending` 会一并清空，所以打断后再说同一句话不会被去重掉。
        """
        with self._tts_lock:
            self._tts_gen += 1          # 旧代号的排队项全部作废
            while True:
                try:
                    old, _gen = self._tts_queue.get_nowait()
                    self._tts_queue.task_done()
                    self._tts_pending.discard(old)
                except queue.Empty:
                    break
            self._tts_pending.clear()
        engine = self._tts_engine
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass

    @property
    def tts_stats(self):
        """排查用：engine 创建次数 + 最近一次错误。"""
        return {"inits": self._tts_inits, "last_error": self._last_tts_error,
                "pending": len(self._tts_pending)}

    def _get_tts_engine(self):
        """取 engine：默认复用同一个实例，只有出错后才重建。

        pyttsx3.init() 会枚举 SAPI 语音（约 100~300 ms），每句都 init 是之前
        "点了没反应、过一会儿才出声"的主因。因此默认复用；若某些机器上复用后
        第二句不出声，把 config.TTS_REUSE_ENGINE 改成 False 即可回到每句新建。
        """
        if self._tts_engine is not None:
            return self._tts_engine
        engine = self._tts_factory()
        try:
            engine.setProperty("rate", getattr(config, "TTS_RATE", 180))
        except Exception:
            pass
        self._tts_engine = engine
        self._tts_inits += 1
        return engine

    def _tts_loop(self):
        """常驻工作线程：COM 在本线程初始化；engine 按需创建/复用。"""
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass

        while True:
            text, gen = self._tts_queue.get()
            try:
                # 打断之前排进来的句子直接丢弃（gen 已被 stop_speaking 递增）
                if gen != self._tts_gen:
                    continue

                reuse = getattr(config, "TTS_REUSE_ENGINE", True)
                if not reuse:
                    self._tts_engine = None      # 强制每句新建（老行为）
                engine = self._get_tts_engine()
                engine.say(text)
                engine.runAndWait()
                self._last_tts_error = None
                if not reuse:
                    self._dispose_engine()
            except Exception as e:
                # 不静默吞掉：记录最后一次错误，并丢弃 engine 让下次重建
                self._last_tts_error = "%s: %s" % (type(e).__name__, e)
                self._dispose_engine()
            finally:
                with self._tts_lock:
                    self._tts_pending.discard(text)
                self._tts_queue.task_done()

    def _dispose_engine(self):
        engine, self._tts_engine = self._tts_engine, None
        if engine is None:
            return
        try:
            engine.stop()
        except Exception:
            pass

    # ================= STT =================
    def _record_to_wav(self, timeout=8.0, phrase_limit=6.0):
        """录一段麦克风音频到临时 WAV。返回 (wav_path, error_code)。"""
        try:
            import speech_recognition as sr
        except Exception:
            return None, "engine"

        mic = None
        try:
            mic = sr.Microphone()
            r = sr.Recognizer()
            with mic as source:
                try:
                    r.adjust_for_ambient_noise(source, duration=0.4)
                except Exception:
                    pass
                audio = r.listen(source, timeout=timeout,
                                 phrase_time_limit=phrase_limit)
        except Exception as e:
            name = type(e).__name__
            if "WaitTimeout" in name or "Timeout" in name:
                return None, "timeout"
            return None, "no_mic"

        # 写出临时 WAV（16kHz/16bit/mono，SAPI 可识别）
        try:
            fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="stt_")
            with os.fdopen(fd, "wb") as f:
                f.write(audio.get_wav_data(convert_rate=16000, convert_width=2))
            return wav_path, None
        except Exception:
            return None, "unknown"

    def _sapi_recognize_wav(self, wav_path, timeout=8.0):
        """用 Windows SAPI 中文离线引擎识别 WAV 文件，返回文本或 None。

        注意：必须在调用线程内 CoInitialize / 消息泵，COM 对象不能跨线程用。
        """
        try:
            import pythoncom
            import win32com.client
        except Exception:
            return None

        pythoncom.CoInitialize()
        try:
            recognizer = win32com.client.Dispatch("SAPI.SpInprocRecognizer")
            ctx = recognizer.CreateRecoContext()
            events = win32com.client.WithEvents(ctx, _SapiRecoEvents)

            grammar = ctx.CreateGrammar(0)
            grammar.DictationLoad("", 0)
            grammar.DictationSetState(1)  # SGDSActive

            fs_in = win32com.client.Dispatch("SAPI.SpFileStream")
            fs_in.Open(wav_path, 0)  # SSFMOpenForRead
            recognizer.AudioInputStream = fs_in

            deadline = time.time() + timeout
            while time.time() < deadline:
                pythoncom.PumpWaitingMessages()
                if events.text:
                    break
                if events.finished:
                    # 给识别事件一点缓冲时间再退出
                    time.sleep(0.2)
                    pythoncom.PumpWaitingMessages()
                    if events.text:
                        break
                    break
                time.sleep(0.03)
            return events.text
        except Exception:
            return None
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _google_recognize(self, wav_path):
        """可选：Google 在线识别（网络通时准确率更高），失败返回 None。"""
        try:
            import speech_recognition as sr
            r = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio = r.record(source)
            return r.recognize_google(audio, language=config.STT_LANGUAGE)
        except Exception:
            return None

    def _recognize_wav_file(self, wav_path):
        """把一个 WAV 文件送进后端识别。返回 (text, error_code)。

        与录音方式解耦：不管是"整段录完"还是"边录边推的最后一段"，
        最终都走这里，保证两条路径的降级逻辑完全一致。
        """
        text = None
        backend = self._online_backend
        online_attempted = False

        # ---- 在线优先 ----
        if backend == "teleai" and self._teleai is not None \
                and self._teleai.configured:
            online_attempted = True
            online_text, _err = self._teleai.recognize(wav_path)
            if online_text:
                text = online_text
                self._last_backend_used = "teleai"
        elif backend == "baidu" and self._baidu is not None \
                and self._baidu.configured:
            online_attempted = True
            online_text, _err = self._baidu.recognize(wav_path)
            if online_text:
                text = online_text
                self._last_backend_used = "baidu"
        elif backend == "google":
            online_attempted = True
            online_text = self._google_recognize(wav_path)
            if online_text:
                text = online_text
                self._last_backend_used = "google"

        # ---- 在线失败 / 未配置 → 离线 SAPI 兜底 ----
        if not text:
            text = self._sapi_recognize_wav(wav_path)
            if text:
                self._last_backend_used = (
                    "sapi(降级)" if online_attempted else "sapi")

        if text:
            return text.strip(), None
        return None, "no_result"

    def _recognize_wav_bytes(self, wav_bytes):
        """内存 WAV → 文本。离线 SAPI 只吃文件，所以这里落一次临时文件。"""
        wav_path = None
        try:
            fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="stt_")
            with os.fdopen(fd, "wb") as f:
                f.write(wav_bytes)
            return self._recognize_wav_file(wav_path)
        except Exception:
            return None, "unknown"
        finally:
            if wav_path:
                try:
                    os.remove(wav_path)
                except Exception:
                    pass

    def recognize_speech(self, timeout=8.0):
        """识别一段语音（录完再识别，老接口）。

        返回 (text, error_code)：
          成功 → ("水杯", None)
          失败 → (None, "no_mic"/"timeout"/"no_result"/"engine"/"unknown")

        注意：此方法会阻塞，应在子线程中调用。新代码建议用 `listen()`，
        它能返回音量、中间结果，并且可以中途取消。
        """
        wav_path, err = self._record_to_wav(timeout=timeout)
        if err:
            return None, err
        try:
            return self._recognize_wav_file(wav_path)
        finally:
            try:
                os.remove(wav_path)
            except Exception:
                pass

    # ================= 流式聆听（B3） =================
    def can_stream(self):
        """当前后端是否支持边录边推。"""
        return bool(getattr(config, "STT_STREAMING", True)
                    and self._online_backend == "teleai"
                    and self._teleai is not None
                    and self._teleai.configured)

    def listen(self, on_final=None, on_level=None, on_partial=None,
               on_error=None, timeout=None, phrase_limit=None, vad=True):
        """开始一次聆听，立刻返回句柄（不阻塞）。

        参数:
            on_final(text)      识别完成，回调最终文本
            on_level(level)     实时音量 0.0~1.0（已节流到 ~LEVEL_INTERVAL_MS）
            on_partial(text)    中间结果（仅流式后端有）
            on_error(code)      失败，code 见 ERRORS
            timeout             等用户开口的最长秒数（默认 config.STT_TIMEOUT）
            phrase_limit        单句最长秒数（默认 config.STT_PHRASE_LIMIT）
            vad                 是否自动断句（按音量判断说完）
        返回:
            ListenHandle

        **回调在线程里执行**，UI 层必须用 Qt 信号切回主线程再改控件。
        """
        handle = ListenHandle()
        self._last_handle = handle
        t = threading.Thread(
            target=self._listen_loop,
            args=(handle, on_final, on_level, on_partial, on_error,
                  timeout if timeout is not None else config.STT_TIMEOUT,
                  phrase_limit if phrase_limit is not None else config.STT_PHRASE_LIMIT,
                  vad),
            daemon=True, name="stt-stream")
        t.start()
        return handle

    def _listen_loop(self, handle, on_final, on_level, on_partial, on_error,
                     timeout, phrase_limit, vad):
        """聆听线程：按块读麦克风 → 算音量 → 流式推送 → 断句收尾。"""
        def _emit(cb, *args):
            if cb is None:
                return
            try:
                cb(*args)
            except Exception:
                pass

        def _fail(code):
            _emit(on_error, code)
            handle._finish(code)

        try:
            import speech_recognition as sr
        except Exception:
            return _fail("engine")

        sample_rate = getattr(config, "TELEAI_SAMPLE_RATE", 16000)
        chunk_ms = max(20, int(getattr(config, "AUDIO_CHUNK_MS", 100)))
        chunk_frames = int(sample_rate * chunk_ms / 1000.0)

        try:
            mic = sr.Microphone(sample_rate=sample_rate)
        except Exception:
            return _fail("no_mic")

        stream_session = None
        try:
            with mic as source:
                stream = source.stream
                if self.can_stream():
                    try:
                        from asr_teleai import TeleAIStream
                        stream_session = TeleAIStream(
                            self._teleai,
                            on_partial=(lambda t: _emit(on_partial, t)))
                        stream_session.start()
                    except Exception:
                        stream_session = None      # 流式起不来就走整段识别

                pcm_all = bytearray()
                started = False
                silence_ms = 0
                waited = 0.0
                spoken_ms = 0
                last_level_emit = 0.0
                start_level = getattr(config, "VAD_START_LEVEL", 0.02)
                silence_level = getattr(config, "VAD_SILENCE_LEVEL", 0.012)
                silence_limit = getattr(config, "VAD_SILENCE_MS", 700)
                level_interval = getattr(config, "LEVEL_INTERVAL_MS", 80) / 1000.0

                while not handle.cancelled:
                    try:
                        raw = stream.read(chunk_frames, exception_on_overflow=False)
                    except Exception:
                        # 读失败（设备被拔、驱动异常）→ 用已录到的内容兜底
                        break
                    pcm = raw.tobytes() if hasattr(raw, "tobytes") else bytes(raw)
                    if not pcm:
                        continue

                    level = pcm_rms(pcm)
                    now = time.time()
                    if now - last_level_emit >= level_interval:
                        last_level_emit = now
                        _emit(on_level, level)

                    if stream_session is not None and stream_session.active:
                        stream_session.push(pcm)

                    if not started:
                        waited += chunk_ms / 1000.0
                        if not vad:
                            started = True
                        elif is_speech(level, start_level):
                            started = True
                        elif waited >= timeout:
                            break
                        if not started:
                            continue

                    pcm_all.extend(pcm)
                    spoken_ms += chunk_ms
                    if vad:
                        if level >= silence_level:
                            silence_ms = 0
                        else:
                            silence_ms += chunk_ms
                        if silence_ms >= silence_limit:
                            break
                    if spoken_ms >= phrase_limit * 1000:
                        break

                # ---- 收尾 ----
                if handle.cancelled:
                    if stream_session is not None:
                        stream_session.cancel()
                    text = stream_session.partial_text if stream_session else ""
                    if text:
                        _emit(on_final, text)
                        return handle._finish("cancelled")
                    return _fail("cancelled")

                if not started:
                    if stream_session is not None:
                        stream_session.cancel()
                    return _fail("timeout")

                if stream_session is not None:
                    text, err = stream_session.finish()
                    if text:
                        self._last_backend_used = "teleai(流式)"
                        _emit(on_final, text)
                        return handle._finish("ok")
                    # 流式没出结果 → 用已录的音频整段再试一次（SAPI 兜底）
                    if pcm_all:
                        text, err = self._recognize_wav_bytes(
                            pcm_to_wav_bytes(pcm_all, sample_rate))
                        if text:
                            _emit(on_final, text)
                            return handle._finish("ok")
                    return _fail(err or "no_result")

                if not pcm_all:
                    return _fail("no_result")
                text, err = self._recognize_wav_bytes(
                    pcm_to_wav_bytes(pcm_all, sample_rate))
                if text:
                    _emit(on_final, text)
                    return handle._finish("ok")
                return _fail(err or "no_result")
        except Exception:
            if stream_session is not None:
                stream_session.cancel()
            return _fail("unknown")

    def recognize_speech_async(self, callback, timeout=8.0,
                               on_level=None, on_partial=None,
                               cancel_event=None):
        """异步语音识别（契约里的兼容接口）。

        callback(text, error_code) 在**子线程**被调用，
        UI 层必须自行通过 Qt 信号切回主线程再操作控件。

        参数:
            on_level:     实时音量回调 0.0~1.0（约 20 Hz），用来画音量条
            on_partial:   中间结果（仅流式后端有）
            cancel_event: 传一个 threading.Event，set() 即中止本次聆听
        返回:
            ListenHandle（可直接 .cancel() / .wait()）
        """
        def _on_final(text):
            try:
                callback(text, None)
            except Exception:
                pass

        def _on_error(code):
            try:
                callback(None, code)
            except Exception:
                pass

        handle = self.listen(on_final=_on_final, on_level=on_level,
                             on_partial=on_partial, on_error=_on_error,
                             timeout=timeout)

        if cancel_event is not None:
            if cancel_event.is_set():
                handle.cancel()
            else:
                def _watch():
                    cancel_event.wait()
                    handle.cancel()
                threading.Thread(target=_watch, daemon=True,
                                 name="stt-cancel").start()
        return handle

    def cancel(self):
        """中止当前聆听（等同句柄上的 cancel）。可重复调用。"""
        handle = self._last_handle
        if handle is not None:
            handle.cancel()

    def speak_result(self, name, is_unknown):
        """按语义播报：未知 →「我不认识这个东西」；已知 →「这是{name}」。

        同一结果连续出现只会播一次（TTS 去重），不会"卡带"。
        """
        if is_unknown or not name:
            return self.speak("我不认识这个东西")
        return self.speak("这是%s" % name)

    def error_message(self, code):
        return self.ERRORS.get(code, self.ERRORS["unknown"])
