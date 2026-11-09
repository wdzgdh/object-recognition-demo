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


class TeleAIStream:
    """单句流式识别会话：start() → push(pcm) 多次 → finish()。

    与 `TeleAIASR.recognize(wav_path)` 的区别：
        recognize() 是「先录完再上传」，用户说完还要等整段上传完才出结果；
        本类边录边发，**停止说话的同时服务端已经在算最后一段**，
        2 秒的短句端到端通常在 0.5~1.2 s 内返回（实测取决于网络）。

    线程约束：一个实例只能在一个线程里用（内部只有一个 WebSocket）。
    典型用法（在录音线程里）：

        stream = TeleAIStream(asr, on_partial=cb)
        stream.start()
        while 录音中:
            stream.push(chunk)          # 顺带把中间结果回调出去
        text, err = stream.finish()

    取消（用户点了"停止"）：直接 `stream.cancel()`，会发结束帧并关连接，
    已识别到的部分结果仍可通过 `partial_text` 取回。
    """

    def __init__(self, client, on_partial=None):
        self.client = client
        self.on_partial = on_partial
        self._ws = None
        self._buf = bytearray()
        self._texts = []          # 已确认的句段（res_status=3/4）
        self._closed = True
        self.last_error = None

    # ---------- 状态 ----------
    @property
    def active(self):
        return self._ws is not None and not self._closed

    @property
    def partial_text(self):
        return "".join(self._texts)

    # ---------- 生命周期 ----------
    def start(self):
        """建连 + 发开始帧 + 等服务端就绪。失败抛 RuntimeError。"""
        if websocket is None:
            raise RuntimeError("未安装 websocket-client")
        client = self.client
        if not client.apikey:
            raise RuntimeError("未配置天翼 API Key")

        ws = client._connect()
        self._ws = ws
        option = {
            "sample_rate": client.sample_rate,
            "format": "pcm",
            "enable_punctuation": True,
            "enable_inverse_text_normalization": True,
        }
        if client.hotwords:
            option["hotwords"] = client.hotwords[:1000]
        client._send(ws, {
            "req_id": str(uuid.uuid4()),
            "rec_status": 0,
            "option": option,
        })

        deadline = time.time() + client.timeout
        while time.time() < deadline:
            msg = client._parse(ws.recv())
            code = msg.get("code")
            if code not in (None, 10000, 20003):
                raise RuntimeError("服务端返回 %s: %s" % (code, msg.get("message", "")))
            if msg.get("res_status") == 0:
                self._closed = False
                return True
        raise RuntimeError("等待识别就绪超时")

    def push(self, pcm):
        """推一段裸 PCM（16k/16bit/单声道），返回本次实际发出的块数。

        不足一块（200 ms）时先攒着，攒够再发——避免触发服务端的
        "Non-real-time audio data" 限速错误。音频本身是实时到达的，
        所以这里**不需要再 sleep**。
        """
        if not self.active or not pcm:
            return 0
        self._buf.extend(pcm)
        sent = 0
        while len(self._buf) >= CHUNK_BYTES:
            chunk = bytes(self._buf[:CHUNK_BYTES])
            del self._buf[:CHUNK_BYTES]
            self.client._send(self._ws, {
                "rec_status": 1,
                "audio_stream": base64.b64encode(chunk).decode("ascii"),
            })
            sent += 1
        self.poll()
        return sent

    def poll(self, timeout=0.001):
        """非阻塞收消息，把中间结果（res_status=3）通过 on_partial 抛出去。

        返回本次新增的中间文本；没有新消息返回 None。
        连接异常会被吞掉并标记为已关闭——录音线程不该因为网络抖动崩掉。
        """
        if not self.active:
            return None
        client = self.client
        got = None
        try:
            self._ws.settimeout(timeout)
            try:
                raw = self._ws.recv()
            except Exception as e:
                # 超时 = 没消息，正常；其它异常 = 连接坏了
                if "Timeout" not in type(e).__name__:
                    self._mark_closed(str(e))
                return None
            if not raw:
                return None
            msg = client._parse(raw)
            code = msg.get("code")
            if code not in (None, 10000, 20003):
                self.last_error = "%s: %s" % (code, msg.get("message", ""))
                return None
            status = msg.get("res_status")
            if status in (3, 4):
                got = self._absorb(msg)
            if status == 4:
                self._mark_closed(None)
        except Exception as e:
            self._mark_closed(str(e))
        return got

    def finish(self, timeout=None):
        """发结束帧并收最终文本。返回 (text, error)。"""
        if not self.active:
            text = self.partial_text
            return (text, None) if text else (None, self.last_error or "连接已关闭")
        client = self.client
        try:
            if self._buf:                      # 把不足一块的尾巴补发，别丢字
                self.client._send(self._ws, {
                    "rec_status": 1,
                    "audio_stream": base64.b64encode(bytes(self._buf)).decode("ascii"),
                })
                self._buf.clear()
            client._send(self._ws, {"rec_status": 2})

            deadline = time.time() + (timeout or max(client.timeout, 8.0))
            while time.time() < deadline:
                try:
                    self._ws.settimeout(deadline - time.time())
                    raw = self._ws.recv()
                except Exception:
                    break
                msg = client._parse(raw)
                code = msg.get("code")
                if code not in (None, 10000, 20003):
                    self.last_error = "识别错误 %s: %s" % (code, msg.get("message", ""))
                    break
                status = msg.get("res_status")
                if status in (3, 4):
                    self._absorb(msg)
                if status == 4:
                    break
        except Exception as e:
            self.last_error = "%s: %s" % (type(e).__name__, str(e)[:160])
        finally:
            self.cancel()

        text = self.partial_text
        if text:
            return text, None
        return None, self.last_error or "云端未返回识别文本"

    def cancel(self):
        """用户主动停止：发结束帧（尽力而为）后关闭连接，部分结果保留。"""
        if self._ws is not None:
            if not self._closed:
                try:
                    self.client._send(self._ws, {"rec_status": 2})
                except Exception:
                    pass
            try:
                self._ws.close()
            except Exception:
                pass
        self._closed = True
        return self.partial_text

    # ---------- 内部 ----------
    def _absorb(self, msg):
        """把一帧里的 results 并入文本缓冲，返回本次新增的中间文本。"""
        added = []
        for item in (msg.get("data") or {}).get("results") or []:
            t = (item.get("text") or "").strip()
            if not t:
                continue
            if t in self._texts:
                continue
            added.append(t)
            self._texts.append(t)
        if not added:
            return None
        text = "".join(self._texts)
        if self.on_partial:
            try:
                self.on_partial(text)
            except Exception:
                pass
        return "".join(added)

    def _mark_closed(self, reason):
        if reason:
            self.last_error = reason
        self._closed = True
