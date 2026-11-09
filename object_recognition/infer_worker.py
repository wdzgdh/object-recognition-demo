# -*- coding: utf-8 -*-
"""后台推理器：把 ResNet18 前向从 GUI 线程搬到工作线程（B4）。

问题（缺陷 P0-2）：
    原来的实时识别由 GUI 线程里的 QTimer 触发，每 300 ms 阻塞主线程
    40–180 ms（TTA 翻倍）。相机线程又每帧无条件发信号，主线程一忙，
    事件队列就积压，表现为「画面卡一下、数字跳一下」。

设计：
    - QThread 子类，run() 里循环：取最新帧 → 提特征 → 检索 → 发信号
    - submit() 只保存**最新**一帧，旧帧直接丢弃并计数（dropped）
    - 结果通过 Qt 信号回主线程，UI 侧不需要任何线程知识
    - 节奏由 interval_ms 控制：算得快就睡到点，算得慢就连续跑（不排队）

用法（A 侧）：
    self.infer = InferWorker(self.extractor, self.library, self.threshold)
    self.infer.result.connect(self._update_result)
    self.infer.latency.connect(self._on_latency)
    self.infer.start()                 # 开摄像头时
    self.infer.submit(frame)           # 每来一帧就投递
    self.infer.stop()                  # 关摄像头/关窗口时

注意：
    library 的增删应尽量在停止推理时进行；recognize() 内部已按较短长度对齐，
    即使 UI 线程正在增删也不会越界。
"""
import threading
import time
from collections import deque

# 兼容 PySide6 / PyQt5（与项目其它模块保持一致）
try:
    from PySide6.QtCore import QThread, Signal
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QThread, pyqtSignal as Signal

import config
from recognize import recognize


class InferWorker(QThread):
    """后台推理线程。信号均在主线程槽函数里被调用。"""

    result = Signal(dict)     # recognize() 的返回，另加 latency_ms / ts / source
    latency = Signal(int)     # 单次推理耗时（毫秒）
    fps = Signal(float)       # 实际识别频率（次/秒）
    error = Signal(str)       # 中文错误描述

    def __init__(self, extractor, library, threshold=None,
                 interval_ms=None, parent=None):
        super().__init__(parent)
        self.extractor = extractor
        self.library = library
        self._threshold = float(
            threshold if threshold is not None else config.THRESHOLD)
        self._interval_ms = int(
            interval_ms if interval_ms is not None
            else getattr(config, "INFER_INTERVAL_MS", 150))

        self._lock = threading.Lock()
        self._frame = None
        self._running = False
        self._dropped = 0
        self._count = 0
        self._fps = 0.0
        self._stamps = deque(maxlen=10)

    # ---------- 对外接口 ----------
    def submit(self, frame):
        """投递最新一帧（BGR ndarray）。必须极快，不阻塞相机线程。"""
        if frame is None:
            return
        with self._lock:
            if self._frame is not None:
                self._dropped += 1      # 上一帧还没被消费 → 丢掉
            self._frame = frame

    def set_threshold(self, threshold):
        self._threshold = float(threshold)

    def set_interval(self, interval_ms):
        self._interval_ms = max(30, int(interval_ms))

    @property
    def dropped(self):
        with self._lock:
            return self._dropped

    @property
    def fps_value(self):
        return self._fps

    @property
    def processed(self):
        return self._count

    # ---------- 线程主体 ----------
    def run(self):
        self._running = True
        try:
            self.extractor.warmup()
        except Exception:
            pass

        while self._running:
            frame = None
            with self._lock:
                if self._frame is not None:
                    frame, self._frame = self._frame, None
            if frame is None:
                self.msleep(8)
                continue

            t0 = time.perf_counter()
            try:
                feat = self.extractor.extract(frame)
                res = recognize(feat, self.library, self._threshold)
            except Exception as e:
                self.error.emit("识别失败：%s: %s" % (type(e).__name__, e))
                self.msleep(50)
                continue

            cost_ms = int((time.perf_counter() - t0) * 1000)
            res["latency_ms"] = cost_ms
            res["ts"] = time.time()
            res["source"] = "camera"

            self.result.emit(res)
            self.latency.emit(cost_ms)

            self._count += 1
            self._stamps.append(time.time())
            if len(self._stamps) >= 2:
                span = self._stamps[-1] - self._stamps[0]
                if span > 0:
                    self._fps = (len(self._stamps) - 1) / span
                    self.fps.emit(round(self._fps, 1))

            # 控制节奏：算得快就补睡，算得慢就立刻接下一帧
            rest = self._interval_ms - cost_ms
            if rest > 0:
                self.msleep(rest)

    def stop(self):
        """停止并等待线程退出（≤ 1.5 s），可重复调用。"""
        self._running = False
        with self._lock:
            self._frame = None
        if self.isRunning():
            self.wait(1500)
