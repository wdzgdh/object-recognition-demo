# -*- coding: utf-8 -*-
"""天翼 AI 开放平台「实时语音识别(超多方言)」适配器（WebSocket 流式）。

官方文档：https://www.teleai.com.cn/doc/QVY8etHoYdG4XoW4Nxvz/kFlF4Fsz7TH6VAmbBFIA

交互流程（三段式）：
  1) rec_status=0 开始识别（携带 req_id + option）
  2) rec_status=1 分块推送 base64(PCM)；按 200ms 节奏发送，避免 10007
     "Non-real-time audio data" 错误
  3) rec_status=2 结束语音流 → 接收 res_status=3/4 的识别文本

鉴权：Bearer Token（长效 API Key），请求头带 X-APP-ID / Device-Uuid。
音频要求：16kHz / 16bit / 单声道 / 裸 PCM（本模块自动从 WAV 剥离头部）。

本模块只负责在线识别，失败一律返回 (None, error)，由 speech.py 降级到
离线 SAPI，保证课堂演示不中断。
"""
import base64
import json
import time
import uuid
import wave

try:
    import websocket  # websocket-client
except ImportError:  # pragma: no cover
    websocket = None

WS_URL = "wss://api-maas.teleai.com.cn/aipaas/voice/v1/realtimeAsrMultiZk/asr"

# 16kHz * 16bit * 1ch = 32000 字节/秒；200ms = 6400 字节
BYTES_PER_SEC = 32000
CHUNK_MS = 200
CHUNK_BYTES = BYTES_PER_SEC * CHUNK_MS // 1000


class TeleAIASR:
    def __init__(self, app_id="", apikey="", appkey="", hotwords=None,
                 timeout=10.0, sample_rate=16000):
        self.app_id = (app_id or "").strip()
        self.apikey = (apikey or "").strip()
        self.appkey = (appkey or "").strip()   # 标准签名鉴权预留，当前用 Bearer
        self.hotwords = list(hotwords or [])
        self.timeout = timeout
        self.sample_rate = sample_rate
        self.last_error = None
        self._device_uuid = uuid.uuid4().hex

    @property
    def configured(self):
        return websocket is not None and bool(self.apikey)

    # ---------- 音频准备 ----------
    @staticmethod
    def _wav_to_pcm(wav_path, expect_rate=16000):
        """从 WAV 文件取裸 PCM 数据，并校验格式。返回 (pcm_bytes, error)。"""
        try:
            with wave.open(wav_path, "rb") as wf:
                rate = wf.getframerate()
                channels = wf.getnchannels()
                width = wf.getsampwidth()
                pcm = wf.readframes(wf.getnframes())
        except Exception as e:
            return None, "读取录音失败: %s" % e
        if channels != 1 or width != 2 or rate != expect_rate:
            return None, ("音频格式不符：需 %dHz/16bit/单声道，实际 %dHz/%dbit/%d声道"
                          % (expect_rate, rate, width * 8, channels))
        if not pcm:
            return None, "录音数据为空"
        return pcm, None

    # ---------- 连接 ----------
    def _connect(self):
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.apikey,
            "Device-Uuid": self._device_uuid,
        }
        if self.app_id:
            headers["X-APP-ID"] = self.app_id
        return websocket.create_connection(
            WS_URL, header=headers, timeout=self.timeout,
            enable_multithread=True,
        )

    @staticmethod
    def _send(ws, obj):
        ws.send(json.dumps(obj, ensure_ascii=False))

    @staticmethod
    def _parse(raw):
        try:
            return json.loads(raw)
        except Exception:
            return {}

    # ---------- 主流程 ----------
    def recognize(self, wav_path):
        """识别 WAV。返回 (text, error_message)，成功时 error 为 None。"""
        self.last_error = None
        if websocket is None:
            return None, "未安装 websocket-client"
        if not self.apikey:
            return None, "未配置天翼 API Key"

        pcm, err = self._wav_to_pcm(wav_path, self.sample_rate)
        if err:
            self.last_error = err
            return None, err

        ws = None
        try:
            ws = self._connect()
            req_id = str(uuid.uuid4())

            # 1) 开始识别
            option = {
                "sample_rate": self.sample_rate,
                "format": "pcm",
                "enable_punctuation": True,
                "enable_inverse_text_normalization": True,
            }
            if self.hotwords:
                option["hotwords"] = self.hotwords[:1000]
            self._send(ws, {
                "req_id": req_id,
                "rec_status": 0,
                "option": option,
            })

            # 等待服务端"识别就绪" (res_status=0)
            ready_deadline = time.time() + self.timeout
            ready = False
            while time.time() < ready_deadline:
                msg = self._parse(ws.recv())
                code = msg.get("code")
                if code not in (None, 10000, 20003):
                    raise RuntimeError("服务端返回 %s: %s" %
                                       (code, msg.get("message", "")))
                if msg.get("res_status") == 0:
                    ready = True
                    break
            if not ready:
                raise RuntimeError("等待识别就绪超时")

            # 2) 按实时节奏分块推送 PCM
            for i in range(0, len(pcm), CHUNK_BYTES):
                chunk = pcm[i:i + CHUNK_BYTES]
                self._send(ws, {
                    "rec_status": 1,
                    "audio_stream": base64.b64encode(chunk).decode("ascii"),
                })
                time.sleep(len(chunk) / float(BYTES_PER_SEC))

            # 3) 结束语音流
            self._send(ws, {"rec_status": 2})

            # 收取最终结果
            texts = []
            result_deadline = time.time() + max(self.timeout, 8.0)
            while time.time() < result_deadline:
                try:
                    msg = self._parse(ws.recv())
                except Exception:
                    break
                code = msg.get("code")
                if code not in (None, 10000, 20003):
                    raise RuntimeError("识别错误 %s: %s" %
                                       (code, msg.get("message", "")))
                if msg.get("res_status") in (3, 4):
                    for item in (msg.get("data") or {}).get("results") or []:
                        t = (item.get("text") or "").strip()
                        if t and (not texts or texts[-1] != t):
                            texts.append(t)
                if msg.get("res_status") == 4:
                    break

            if texts:
                return "".join(texts), None
            self.last_error = "云端未返回识别文本"
            return None, self.last_error
        except Exception as e:
            self.last_error = "%s: %s" % (type(e).__name__, str(e)[:160])
            return None, self.last_error
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
