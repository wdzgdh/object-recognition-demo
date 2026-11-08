# -*- coding: utf-8 -*-
"""摄像头采集：支持本地摄像头与手机网络摄像头（MJPEG / RTSP）。

OpenCV 的 cv2.VideoCapture 既接受整数索引（本地摄像头），也接受 URL
字符串（HTTP MJPEG 流 / RTSP 流），因此手机摄像头只需把"源"换成 URL：

  - Android「IP Webcam」+ USB(adb forward)：http://127.0.0.1:8080/video
  - Android「IP Webcam」+ 同一 WiFi：      http://手机IP:8080/video
  - 通用 RTSP 流：                          rtsp://user:pass@手机IP:port/...

支持"自动优先手机、失败回退本地"，以及网络流断开后的自动重连。
"""
import socket
import time
from urllib.parse import urlparse

import cv2

# 兼容 PyQt5 / PySide6
try:
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtCore import QThread, Signal
except ImportError:
    from PyQt5.QtGui import QImage, QPixmap
    from PyQt5.QtCore import QThread, pyqtSignal as Signal


def cv_to_qpixmap(frame_bgr):
    """OpenCV BGR 帧 → QPixmap（用于显示）。"""
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
    return QPixmap.fromImage(img.copy())


def is_url_source(source):
    return isinstance(source, str) and source.strip()


def url_reachable(url, timeout=0.8):
    """快速探测手机摄像头端口是否可达，避免 OpenCV 长时间阻塞。"""
    try:
        u = urlparse(url)
        host = u.hostname
        port = u.port or (443 if u.scheme == "https" else
                          554 if u.scheme == "rtsp" else 80)
        if not host:
            return False
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


class CameraThread(QThread):
    """后台线程持续读取视频帧，通过信号发送给 UI。

    信号:
        frame_bgr(object)  每帧 BGR ndarray（显示 + 识别共用）
        status(str)        连接状态文本（供状态栏显示）
    """
    frame_bgr = Signal(object)
    frame_ready = Signal(QImage)
    status = Signal(str)

    MAX_RECONNECT = 3       # 网络流断开后的最大重连次数
    RECONNECT_WAIT = 0.8    # 每次重连前等待秒数

    def __init__(self, source=0, fallback=None, parent=None):
        """参数:
            source:   主视频源，int（本地索引）或 str（手机 URL）
            fallback: 主源不可用时的回退源，通常为本地摄像头索引 0；
                      None 表示不回退
        """
        super().__init__(parent)
        self.source = source
        self.fallback = fallback
        self._running = False
        self.active_source = None   # 实际生效的视频源

    # ---------- 打开视频源 ----------
    def _open(self, source):
        """尝试打开一个视频源并试读一帧。返回 (cap, first_frame) 或 (None, None)。"""
        try:
            cap = cv2.VideoCapture(source)
            if is_url_source(source):
                # 网络流：缩小缓冲，避免延迟越积越大；限制打开超时
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
                    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000)
            if not cap.isOpened():
                cap.release()
                return None, None
            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                return None, None
            return cap, frame
        except Exception:
            return None, None

    def _label(self, source):
        return "手机摄像头" if is_url_source(source) else "本地摄像头"

    def run(self):
        # 0) 手机未连接时先做快速探测，避免 OpenCV 阻塞数秒
        phone_ready = True
        if is_url_source(self.source):
            phone_ready = url_reachable(self.source)
            if not phone_ready:
                self.status.emit("未检测到手机摄像头，使用本地摄像头")

        # 1) 优先主源
        cap, frame = (self._open(self.source) if phone_ready else (None, None))
        if cap is not None:
            self.active_source = self.source
            self.status.emit("已连接 " + self._label(self.source))
        elif self.fallback is not None and self.fallback != self.source:
            # 2) 主源不可用 → 回退
            cap, frame = self._open(self.fallback)
            if cap is not None:
                self.active_source = self.fallback
                self.status.emit("已回退到 " + self._label(self.fallback))

        if cap is None:
            self.status.emit("摄像头打开失败（请检查手机连接 / 地址 / 本地设备）")
            self.frame_ready.emit(QImage())  # 兼容旧信号语义
            return

        self._running = True
        if frame is not None:
            self.frame_bgr.emit(frame)

        reconnects = 0
        try:
            while self._running:
                ret, frame = cap.read()
                if ret and frame is not None:
                    reconnects = 0
                    self.frame_bgr.emit(frame)
                    continue

                # 读帧失败：网络流尝试重连，本地设备直接结束
                if not is_url_source(self.active_source):
                    break
                cap.release()
                reconnects += 1
                if reconnects > self.MAX_RECONNECT:
                    self.status.emit("手机摄像头连接中断，请检查网络后重新开启")
                    break
                self.status.emit("手机摄像头断流，正在重连 (%d/%d)…"
                                 % (reconnects, self.MAX_RECONNECT))
                time.sleep(self.RECONNECT_WAIT)
                cap, _ = self._open(self.active_source)
                if cap is None:
                    continue
        finally:
            try:
                cap.release()
            except Exception:
                pass

    def stop(self):
        self._running = False
        self.wait(3000)
