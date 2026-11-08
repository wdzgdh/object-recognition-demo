# -*- coding: utf-8 -*-
"""语音交互：语音识别（STT）+ 语音合成（TTS）。

设计要点（针对 Windows + Qt 环境）：

- TTS：pyttsx3 底层是 SAPI5(COM)，engine 必须在**使用它的那个线程**里创建，
  且每次播报用全新 engine（runAndWait 后旧 engine 状态会失效）。
  因此这里用「单一常驻工作线程 + 队列」：所有播报请求排队，worker 线程内
  初始化 COM、每次新建 engine 播报，彻底解决"只能播报一次"的问题。

- STT：speech_recognition 录麦克风 → 存临时 WAV → 交给识别后端。
  后端由 config.STT_BACKEND 决定，且**任何在线后端失败都会自动降级到
  Windows SAPI 中文离线识别**（系统自带 MS-2052-80-DESK），保证演示不中断：
    "teleai" = 天翼 AI 开放平台实时语音识别（WebSocket 流式）
    "baidu"  = 百度短语音 REST API
    "google" = Google 在线识别（国内通常不可达）
    "sapi"   = 纯离线

- 所有 STT 失败都返回结构化错误原因，不再静默吞异常；回调由 UI 层通过
  Qt 信号切回主线程，避免跨线程操作控件。
"""
import os
import queue
import tempfile
import threading
import time

import config


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


class Speech:
    # 错误码 → 用户可读提示
    ERRORS = {
        "no_mic": "未检测到可用麦克风，请检查设备或权限",
        "timeout": "未检测到语音，请靠近麦克风再试",
        "no_result": "没能听清，请说清楚一点（也可直接用键盘输入）",
        "engine": "离线语音引擎不可用，请改用键盘输入",
        "unknown": "语音识别失败，请改用键盘输入",
    }

    def __init__(self):
        # ---- TTS：队列 + 常驻 worker ----
        self._tts_queue = queue.Queue()
        self._tts_worker = threading.Thread(
            target=self._tts_loop, daemon=True, name="tts-worker")
        self._tts_worker.start()
        self._last_tts_error = None

        # ---- 在线 ASR（可选） ----
        self._baidu = None
        self._teleai = None
        self._online_backend = getattr(config, "STT_BACKEND", "sapi")
        self._last_backend_used = "sapi"

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
    def speak(self, text):
        """异步播报文本（可连续多次调用，每次都能出声）。"""
        if text:
            self._tts_queue.put(str(text))

    def _tts_loop(self):
        """常驻工作线程：COM 在本线程初始化，每次播报新建 engine。"""
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass

        while True:
            text = self._tts_queue.get()
            try:
                import pyttsx3
                engine = pyttsx3.init()  # 每次新建，状态干净
                try:
                    engine.setProperty("rate", 180)
                    engine.say(text)
                    engine.runAndWait()
                finally:
                    try:
                        engine.stop()
                    except Exception:
                        pass
                    try:
                        del engine
                    except Exception:
                        pass
                self._last_tts_error = None
            except Exception as e:
                # 不再静默吞掉，记录最后一次错误便于排查
                self._last_tts_error = "%s: %s" % (type(e).__name__, e)
            finally:
                self._tts_queue.task_done()

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

    def recognize_speech(self, timeout=8.0):
        """识别一段语音。

        返回 (text, error_code)：
          成功 → ("水杯", None)
          失败 → (None, "no_mic"/"timeout"/"no_result"/"engine"/"unknown")

        在线后端（百度/Google）优先；失败或未配置时自动降级到离线 SAPI。
        注意：此方法会阻塞，应在子线程中调用。
        """
        wav_path, err = self._record_to_wav(timeout=timeout)
        if err:
            return None, err

        try:
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
        finally:
            try:
                os.remove(wav_path)
            except Exception:
                pass

    def recognize_speech_async(self, callback, timeout=8.0):
        """异步语音识别。callback(text, error_code) 在**子线程**被调用，
        UI 层必须自行通过 Qt 信号切回主线程再操作控件。
        """
        def _run():
            try:
                text, err = self.recognize_speech(timeout=timeout)
            except Exception:
                # 任何未预期异常也要回调，否则 UI 按钮会一直处于禁用态
                text, err = None, "unknown"
            try:
                callback(text, err)
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True, name="stt-worker").start()

    def error_message(self, code):
        return self.ERRORS.get(code, self.ERRORS["unknown"])
