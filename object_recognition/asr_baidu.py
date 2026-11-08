# -*- coding: utf-8 -*-
"""百度短语音识别 REST API 适配器（可选的高精度在线 ASR）。

调用链：
  1) API Key + Secret Key → access_token（有效期通常 30 天，本地缓存）
  2) 16kHz/16bit/单声道 WAV → base64 → POST vop.baidu.com/server_api
  3) err_no==0 时取 result[0] 作为识别文本

设计原则：本模块**只负责识别**，不做兜底策略；网络/鉴权失败由 speech.py
自动降级回离线 SAPI，保证课堂演示不会因断网而完全失效。
"""
import base64
import json
import os
import time
import uuid

import requests

AUTH_URL = "https://aip.baidubce.com/oauth/2.0/token"
ASR_URL = "https://vop.baidu.com/server_api"


class BaiduASR:
    def __init__(self, api_key, secret_key, cache_path=None,
                 timeout=10.0, dev_pid=1537, hotword=""):
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.cache_path = cache_path
        self.timeout = timeout
        self.dev_pid = dev_pid          # 1537 = 普通话(有标点，输入法模型)
        self.hotword = (hotword or "").strip()
        self._cuid = "object_recognition_" + uuid.getnode().__str__()[-8:]
        self.last_error = None

    @property
    def configured(self):
        return bool(self.api_key and self.secret_key)

    # ---------- access_token（带本地缓存） ----------
    def _load_cached_token(self):
        if not self.cache_path or not os.path.exists(self.cache_path):
            return None
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("expires_at", 0) > time.time() + 300 and d.get("token"):
                return d["token"]
        except Exception:
            pass
        return None

    def _save_cached_token(self, token, expires_in):
        if not self.cache_path:
            return
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({"token": token,
                           "expires_at": time.time() + int(expires_in)},
                          f, ensure_ascii=False)
        except Exception:
            pass

    def _fetch_token(self):
        cached = self._load_cached_token()
        if cached:
            return cached
        params = {
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key,
        }
        r = requests.get(AUTH_URL, params=params, timeout=self.timeout)
        r.raise_for_status()
        d = r.json()
        token = d.get("access_token")
        if not token:
            raise RuntimeError("获取 token 失败: %s" % d.get("error_description", d))
        self._save_cached_token(token, d.get("expires_in", 2592000))
        return token

    # ---------- 识别 ----------
    def recognize(self, wav_path):
        """识别 WAV 文件。返回 (text, error_message)，成功时 error 为 None。"""
        self.last_error = None
        if not self.configured:
            return None, "未配置百度 API Key / Secret Key"

        try:
            with open(wav_path, "rb") as f:
                raw = f.read()
            if not raw:
                return None, "录音文件为空"

            token = self._fetch_token()
            body = {
                "format": "wav",
                "rate": 16000,
                "channel": 1,
                "cuid": self._cuid,
                "token": token,
                "dev_pid": self.dev_pid,
                "speech": base64.b64encode(raw).decode("utf-8"),
                "len": len(raw),          # 百度要求：原始音频字节数
            }
            if self.hotword:
                body["hotword"] = self.hotword

            r = requests.post(ASR_URL, json=body, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            if data.get("err_no") == 0:
                texts = data.get("result") or []
                if texts and texts[0].strip():
                    return texts[0].strip(), None
                return None, "云端未返回识别文本"
            msg = "%s (%s)" % (data.get("err_msg", "未知错误"), data.get("err_no"))
            self.last_error = msg
            return None, msg
        except Exception as e:
            self.last_error = "%s: %s" % (type(e).__name__, str(e)[:120])
            return None, self.last_error
