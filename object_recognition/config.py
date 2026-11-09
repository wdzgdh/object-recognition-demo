# -*- coding: utf-8 -*-
"""全局配置项。集中管理阈值、路径、设备等参数。"""
import json
import os

# ---- 路径 ----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")  # 本地缓存（含密钥 token，不提交）

# 特征库目录：默认放项目内，可被环境变量指到云盘同步目录实现跨设备自动同步。
# 例：set OBJECT_REC_LIBRARY_DIR=D:\OneDrive\object_rec_library
# 注意：只放特征库，ASR_TOKEN_CACHE 仍留在 DATA_DIR，不会把密钥同步到云盘。
LIBRARY_DIR = os.environ.get("OBJECT_REC_LIBRARY_DIR", "").strip() or DATA_DIR
LIBRARY_PATH = os.path.join(LIBRARY_DIR, "library.json")   # 元数据（名称/时间等）
FEATS_PATH = os.path.join(LIBRARY_DIR, "library_feats.npy")  # 特征向量（512维）

# ---- 识别参数 ----
THRESHOLD = 0.80          # 相似度阈值，低于则判"未知物体"（界面上可实时调整）
TOP_K = 3                 # 展示 Top-K 候选
IMG_SIZE = 224            # 输入尺寸
FEATURE_DIM = 512         # ResNet18 avgpool 输出维度

# ---- 推理设备 ----
DEVICE = "cuda"  # "cuda" / "cpu"；程序启动时会自动检测，cuda 不可用则回退 cpu

# ---- 摄像头 ----
CAMERA_INDEX = 0          # 本地摄像头索引
CAMERA_FPS = 30           # 实时预览帧率上限
CAMERA_INFER_INTERVAL_MS = 300  # 实时识别间隔（毫秒），避免每帧都推理

# 手机摄像头（网络流）。三种模式：
#   "auto"  = 手机连上就用手机，没连自动用本地摄像头（推荐）
#   "phone" = 只用手机摄像头
#   "local" = 只用本地摄像头
CAMERA_MODE = "auto"
# 手机流地址，支持 HTTP MJPEG / RTSP。也可用环境变量
# OBJECT_REC_PHONE_CAMERA_URL 覆盖（优先级更高）。
#   Android「IP Webcam」+ USB:  adb forward tcp:8080 tcp:8080
#                              → http://127.0.0.1:8080/video
#   Android「IP Webcam」+ WiFi: http://手机IP:8080/video
PHONE_CAMERA_URL = os.environ.get("OBJECT_REC_PHONE_CAMERA_URL", "").strip() \
    or "http://127.0.0.1:8080/video"
CAMERA_CONFIG_PATH = os.path.join(BASE_DIR, "camera_config.json")


def load_camera_config():
    """读取摄像头配置（不含密钥，该文件可提交）。"""
    d = {"mode": CAMERA_MODE, "phone_url": PHONE_CAMERA_URL}
    try:
        if os.path.exists(CAMERA_CONFIG_PATH):
            with open(CAMERA_CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            d["mode"] = saved.get("mode", d["mode"])
            d["phone_url"] = saved.get("phone_url", d["phone_url"])
    except Exception:
        pass
    # 环境变量始终优先，方便临时切换
    env_url = os.environ.get("OBJECT_REC_PHONE_CAMERA_URL", "").strip()
    if env_url:
        d["phone_url"] = env_url
    return d


def save_camera_config(mode, phone_url):
    try:
        with open(CAMERA_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"mode": mode, "phone_url": phone_url},
                      f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

# ---- 语音 ----
# 语音识别后端：
#   "teleai" = 天翼 AI 开放平台「实时语音识别(超多方言)」WebSocket（默认，高精度）
#   "baidu"  = 百度短语音识别 REST API
#   "google" = Google 在线识别（国内网络通常不可达）
#   "sapi"   = Windows 自带中文离线识别引擎（无需联网、无需 key）
# 任何在线后端失败都会自动降级到 sapi。
STT_BACKEND = "teleai"
STT_LANGUAGE = "zh-CN"
STT_TIMEOUT = 8.0        # 等待说话的最长秒数
STT_PHRASE_LIMIT = 6.0   # 单次录音最长秒数
# 语音合成：pyttsx3 离线 TTS（SAPI5），由 speech.py 的常驻线程驱动

# ---- 在线 ASR 凭证 ----
# 凭证读取优先级：环境变量 > 本地配置文件 asr_config.json
# 该文件含密钥，不要公开 / 提交到代码仓库。
ASR_CONFIG_PATH = os.path.join(BASE_DIR, "asr_config.json")
ASR_TOKEN_CACHE = os.path.join(DATA_DIR, ".baidu_token.json")

# 天翼 AI 开放平台
TELEAI_TIMEOUT = 12.0        # WebSocket 操作超时（秒）
TELEAI_SAMPLE_RATE = 16000   # 必须与录音一致
TELEAI_HOTWORDS = []         # 可选热词，如 ["水杯", "键盘"]

# 百度短语音识别
BAIDU_DEV_PID = 1537
BAIDU_TIMEOUT = 10.0
BAIDU_HOTWORD = ""


def _read_config():
    """合并配置：可提交的明文配置（非敏感项） + 用户目录里的 DPAPI 加密凭证。

    优先顺序在 load_teleai_credentials / load_asr_keys 中最终确定为：
        环境变量 > 加密凭证 > 明文配置文件
    """
    d = {}
    # 1) 项目内配置：只应包含 provider / hotwords 等非敏感项
    try:
        if os.path.exists(ASR_CONFIG_PATH):
            with open(ASR_CONFIG_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
    except Exception:
        d = {}

    # 2) 用户目录的加密凭证（覆盖同名空字段）
    try:
        import credentials
        encrypted = credentials.load() or {}
        for section in ("teleai", "baidu"):
            sec = encrypted.get(section) or {}
            if not sec:
                continue
            d.setdefault(section, {})
            for k, v in sec.items():
                if v:
                    d[section][k] = v
    except Exception:
        pass
    return d


def load_teleai_credentials():
    """返回 (app_id, appkey, apikey, hotwords)。凭证不写死在代码里。"""
    d = _read_config()
    t = d.get("teleai") or {}
    app_id = os.environ.get("TELEAI_APP_ID", "").strip() or (t.get("app_id") or "").strip()
    appkey = os.environ.get("TELEAI_APPKEY", "").strip() or (t.get("appkey") or "").strip()
    apikey = os.environ.get("TELEAI_APIKEY", "").strip() or (t.get("apikey") or "").strip()
    hotwords = t.get("hotwords") or TELEAI_HOTWORDS
    if isinstance(hotwords, str):
        hotwords = [x.strip() for x in hotwords.split(",") if x.strip()]
    return app_id, appkey, apikey, list(hotwords)


def load_asr_keys():
    """百度凭证，返回 (api_key, secret_key, hotword)。兼容新旧两种配置结构。"""
    d = _read_config()
    b = d.get("baidu") or {}
    key = os.environ.get("BAIDU_ASR_API_KEY", "").strip() \
        or (b.get("api_key") or d.get("api_key") or "").strip()
    secret = os.environ.get("BAIDU_ASR_SECRET_KEY", "").strip() \
        or (b.get("secret_key") or d.get("secret_key") or "").strip()
    hotword = (b.get("hotword") or d.get("hotword") or BAIDU_HOTWORD or "").strip()
    return key, secret, hotword


# ---- 实时推理 ----
# 推理已从 GUI 线程移到 InferWorker 工作线程，因此间隔可以更短；
# 主线程不再被前向计算阻塞，150ms 也不会卡界面。
INFER_INTERVAL_MS = 150

# ---- 界面行为（A 线用 getattr 兜底读取，键名见 docs/CONTRACT.md 第 5 节）----
AUTO_SPEAK = True          # 识别成功后自动语音播报
HISTORY_MAX = 20           # 结果历史保留条数
SHOW_OVERLAY = True        # 预览左上角显示 FPS / 推理耗时 / 结果

# ---- 识别质量 ----
ROI_RATIO = 0.75           # 中心 ROI 裁切比例，1.0 表示不裁（注册与实时走同一路径）
SMOOTH_N = 3               # 实时结果连续 N 帧一致才上屏，抑制名称跳动

# ---- 持久化的界面状态 ----
THRESHOLD_STORE = os.path.join(DATA_DIR, "ui_state.json")


def load_ui_state():
    """读取界面状态（阈值等）。文件不存在或损坏时返回 {}，绝不抛异常。"""
    try:
        if os.path.exists(THRESHOLD_STORE):
            with open(THRESHOLD_STORE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_ui_state(**kwargs):
    """合并写入界面状态。返回 True/False，失败不影响程序运行。"""
    try:
        data = load_ui_state()
        data.update(kwargs)
        os.makedirs(os.path.dirname(THRESHOLD_STORE), exist_ok=True)
        tmp = THRESHOLD_STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, THRESHOLD_STORE)
        return True
    except Exception:
        return False


def get_threshold():
    """取生效阈值：界面保存过就用保存值，否则用默认 THRESHOLD。"""
    try:
        v = float(load_ui_state().get("threshold", THRESHOLD))
    except Exception:
        return THRESHOLD
    return min(max(v, 0.05), 0.99)


# 确保数据目录与特征库目录都存在（云盘目录可能尚不存在）
os.makedirs(DATA_DIR, exist_ok=True)
try:
    os.makedirs(LIBRARY_DIR, exist_ok=True)
except OSError:
    # 云盘目录不可写时回退到项目内，保证程序仍能启动
    LIBRARY_DIR = DATA_DIR
    LIBRARY_PATH = os.path.join(DATA_DIR, "library.json")
    FEATS_PATH = os.path.join(DATA_DIR, "library_feats.npy")
