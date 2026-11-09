# -*- coding: utf-8 -*-
"""音频小工具（B3）：PCM 音量、内存 WAV 封装、简单的静音判定。

为什么不用 `speech_recognition` 现成的 `listen()`：
    那个接口是"录完再返回"，中途拿不到音量、也没法取消。要做「按住说话 /
   聆听到一半取消 / 实时音量条」，必须自己按块读 `AudioSource.stream`，
    所以这里提供一组**纯函数**，方便单测（不碰麦克风、不碰网络）。
"""
import io
import math
import struct
import wave

# 16bit 单声道满量程
_FULL_SCALE = 32768.0


def pcm_rms(pcm):
    """裸 PCM(int16 小端) 的均方根音量，归一化到 0.0~1.0。

    参数:
        pcm: bytes / bytearray / memoryview
    返回:
        float，0.0 = 完全静音，1.0 = 满幅（实际语音约 0.01~0.3）
    """
    if not pcm:
        return 0.0
    usable = len(pcm) - (len(pcm) % 2)          # 丢掉可能存在的半个采样
    if usable <= 0:
        return 0.0
    count = usable // 2
    try:
        samples = struct.unpack("<%dh" % count, bytes(pcm[:usable]))
    except struct.error:
        return 0.0
    total = 0
    for s in samples:
        total += s * s
    return min(1.0, math.sqrt(total / float(count)) / _FULL_SCALE)


def pcm_duration_ms(pcm, sample_rate=16000, width=2):
    """根据字节数反推时长（毫秒）。"""
    if sample_rate <= 0 or width <= 0:
        return 0
    return int(len(pcm) / float(sample_rate * width) * 1000)


def pcm_to_wav_bytes(pcm, sample_rate=16000, width=2, channels=1):
    """把裸 PCM 封成内存里的 WAV 字节，免去临时文件。

    离线 SAPI 只能吃文件，但百度 REST 可以直接传内存字节，
    统一走这个函数，两条后端拿到的格式完全一致。
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(pcm))
    return buf.getvalue()


def wav_bytes_to_pcm(data, expect_rate=16000):
    """WAV 字节 → 裸 PCM；顺带校验格式。返回 (pcm, error)。"""
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            rate = wf.getframerate()
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            pcm = wf.readframes(wf.getnframes())
    except Exception as e:
        return None, "WAV 解析失败: %s" % e
    if channels != 1 or width != 2 or rate != expect_rate:
        return None, ("音频格式不符：需 %dHz/16bit/单声道，实际 %dHz/%dbit/%d声道"
                      % (expect_rate, rate, width * 8, channels))
    return pcm, None


def is_speech(level, threshold):
    """音量是否达到"算在说话"的门限。抽出来是为了让 VAD 的阈值有单测。"""
    return level >= threshold
