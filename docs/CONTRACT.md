# 接口契约（A/B 边界）

> 两人并行开发时，**这份文件就是合同**。签名不一致 = 集成失败，所以先定签名再写实现。
> 修改契约必须单独提 PR，并在描述里 @ 对方。

---

## 1. `infer_worker.py`（B 交付，A 只调用）

推理从 GUI 线程搬到工作线程。A 侧只做三件事：`new` → `connect` → `start/stop`。

```python
# infer_worker.py
import numpy as np
from PySide6.QtCore import QObject, Signal

class InferWorker(QObject):
    """后台推理器：内部持有 FeatureExtractor + FeatureLibrary 引用。

    线程模型：QThread + QObject.moveToThread；submit() 只保存最新帧（不排队），
    定时器在 worker 线程内触发推理，结果通过信号回主线程。
    """

    result = Signal(dict)     # 与 recognize.recognize() 返回同结构，另加 latency_ms / ts
    latency = Signal(int)     # 单次推理耗时（毫秒），供 UI 叠层显示
    fps = Signal(float)       # 实际识别频率（次/秒）
    error = Signal(str)       # 中文错误描述，UI 弹提示用

    def __init__(self, extractor, library, threshold: float = 0.8,
                 parent=None) -> None: ...

    def start(self, interval_ms: int = 300) -> None:
        """启动工作线程。"""

    def submit(self, frame: np.ndarray | None) -> None:
        """投递最新一帧（BGR ndarray）。非阻塞；只保留最后一帧，旧帧丢弃。"""

    def set_threshold(self, threshold: float) -> None:
        """滑条改动时调用，线程安全。"""

    def stop(self) -> None:
        """停止并 join 线程（≤ 1 s），可重复调用。"""
```

**A 侧接线（`ui/app.py`）**

```python
from infer_worker import InferWorker

self.infer = InferWorker(self.extractor, self.library, self.threshold)
self.infer.result.connect(self._update_result)      # 复用现有结果渲染
self.infer.latency.connect(self._on_latency)        # A4 叠层
self.infer.error.connect(lambda m: self.status.showMessage(m))

# 相机帧到达时
self.infer.submit(frame)

# 开关摄像头
self.infer.start(config.CAMERA_INFER_INTERVAL_MS)   # 开
self.infer.stop()                                   # 关
```

**约束**
- `submit()` 必须极快（< 1 ms），不得阻塞相机线程。
- `result` 里的 `latency_ms` 由 B 填；A 只显示，不计算。
- 若 B4 未合入，A 的 `app.py` 保留旧的 `_infer_timer` 分支，用 `try/except ImportError` 降级——**A2/A3 可以先合**。

---

## 2. `Speech` 扩展（B 交付，A 调用）

现有 `recognize_speech_async(callback, timeout)` 保留签名兼容，新增可选参数与两个方法。

```python
class Speech:
    def recognize_speech_async(
        self,
        callback: Callable[[str | None, str | None], None],   # (text, error_code)
        timeout: float = 8.0,
        on_level: Callable[[float], None] | None = None,      # 0.0~1.0 实时音量，约 20 Hz
        on_partial: Callable[[str], None] | None = None,      # 云端增量文本
        cancel_event: "threading.Event | None" = None,        # set() 即中止录音/上传
    ) -> None: ...

    def cancel(self) -> None:
        """中止当前聆听（等同 set(cancel_event)）。可重复调用。"""

    def speak_result(self, name: str, is_unknown: bool) -> None:
        """按语义播报：未知 →「我不认识这个东西」；已知 →「这是{name}」。"""

    @property
    def backend_status(self) -> str:
        """保持现有的 backend_status() 方法不变，不要改签名。"""
```

**约束**
- `on_level` / `on_partial` 都在**工作线程**回调，A 必须用 Qt 信号切回主线程再更新控件（沿用现有 `stt_result` 模式）。
- `cancel()` 后必须回调 `callback(None, "cancelled")`，A 据此恢复按钮状态。
- 错误码新增 `"cancelled"`，其余码不变。

---

## 3. `CameraThread` 扩展（B 交付，A 读取）

```python
class CameraThread(QThread):
    frame_bgr = Signal(object)     # 不变
    status = Signal(str)           # 不变

    @property
    def fps(self) -> float: ...        # 采集帧率，供叠层
    @property
    def dropped(self) -> int: ...      # 累计丢帧数（UI 忙时主动丢弃）
```

`frame_bgr` 语义变更（重要）：**不再保证每帧都发**。当 UI 未消费时相机线程丢弃旧帧，只投递最新帧。A 侧无需改动，但不能再假设"收到的帧是连续的"。

---

## 4. `FeatureLibrary` 扩展（B 交付，A 调用）

```python
class FeatureLibrary:
    def validate(self) -> tuple[bool, str]:
        """校验元数据与特征矩阵是否一致。返回 (ok, 中文说明)。"""

    def stats(self) -> dict:
        """{'objects': int, 'images': int, 'dim': int, 'bytes': int, 'path': str}"""

    def rename(self, old_name: str, new_name: str) -> int:
        """重命名物体，返回受影响的特征条数。"""

    def delete_one(self, name: str, index: int) -> bool:
        """删除某个物体的第 index 张特征（A3 单张删除用）。"""

    def thumb_paths(self, name: str) -> list[str]:
        """该物体的缩略图路径列表；不存在的图片返回空列表。"""
```

**约束**：`rename` / `delete_one` 之后必须内部 `_save()`，A 不用手动保存。
`thumb_paths` 依赖 P1 新增的"录入时保存原图副本到 `data/thumbs/`"——由 B 在 B2 里落地，A3 直接读。

---

## 5. 配置键（B 在 `config.py` 里新增；A 用 getattr 兜底）

| 键 | 类型 | 默认 | 用途 | 谁用 |
|---|---|---|---|---|
| `AUTO_SPEAK` | bool | `True` | 识别后自动播报 | A1 |
| `HISTORY_MAX` | int | `20` | 结果历史保留条数 | A4 |
| `SHOW_OVERLAY` | bool | `True` | 预览叠层开关 | A4 |
| `ROI_RATIO` | float | `0.75` | 中心 ROI 裁切比例（1.0=不裁） | B5 |
| `SMOOTH_N` | int | `3` | 连续 N 帧一致才上屏 | B5 |
| `INFER_INTERVAL_MS` | int | `150` | 推理间隔（比旧的 300 ms 更快，因为已不阻塞 UI） | B4 |
| `THRESHOLD_STORE` | str | `data/ui_state.json` | 阈值持久化位置 | B1 |

**A 侧读法（必须这么写，保证 B1 未合入时也能跑）**

```python
auto_speak = getattr(config, "AUTO_SPEAK", True)
```

---

## 6. 结果字典（`result`）字段

保持现有结构向后兼容，只新增字段：

```python
{
    "name": "水杯",            # 已知类名 或 "未知物体"
    "score": 0.91,             # 该类聚合分
    "raw_score": 0.91,         # 该类最大相似度（判定依据）
    "is_unknown": False,
    "top_k": [{"name": "水杯", "score": 0.91}, ...],
    # 新增
    "latency_ms": 62,          # 本次推理耗时
    "ts": 1731091200.0,        # 时间戳
    "source": "camera",        # "camera" | "file"
}
```

**判定口径统一（B5 必须做到）**：界面显示的分数与"是否未知"的判定必须用**同一个数**。现状是判定用 `raw_score`、Top-3 显示聚合分，`top_agg>1` 时会出现"0.72 却判为已知"的矛盾。约定：**一律以 `raw_score` 为准**，`top_k` 里也放 `raw_score` 口径的分数。

---

## 7. 失败处理约定

| 场景 | 谁处理 | 行为 |
|---|---|---|
| 图片无法解码 | A | 跳过该张，状态栏提示"第 N 张无法读取"，不中断整批 |
| 特征库行数不一致 | B | `validate()` 返回 False，截断到 `min` 并在 UI 顶部黄条提示 |
| 模型加载失败 | A | 弹中文对话框："模型权重加载失败，请检查网络或手动放置权重"，程序不退出 |
| 语音后端不可用 | B | 自动降级 SAPI，`backend_status` 显示实际引擎 |
| 第二实例启动 | B | 文件锁失败 → 提示"程序已在运行"并退出 |
