# -*- coding: utf-8 -*-
"""音频小工具测试（B3）：纯函数，不碰麦克风。"""
import struct

import pytest

from utils.audio import (
    is_speech, pcm_duration_ms, pcm_rms, pcm_to_wav_bytes, wav_bytes_to_pcm)


def _pcm(amplitude, samples=160):
    """生成 samples 个恒定幅值的 int16 采样。"""
    return struct.pack("<%dh" % samples, *([amplitude] * samples))


def test_rms_silence_is_zero():
    assert pcm_rms(_pcm(0)) == 0.0
    assert pcm_rms(b"") == 0.0
    assert pcm_rms(None) == 0.0


def test_rms_full_scale_is_one():
    # -32768 是 int16 的下界，满幅方波 → 归一化后 ≈ 1.0
    assert pcm_rms(_pcm(-32768)) == pytest.approx(1.0, abs=1e-6)


def test_rms_half_scale():
    assert pcm_rms(_pcm(16384)) == pytest.approx(0.5, abs=1e-3)


def test_rms_odd_length_bytes_ignored():
    """多出的半个采样不能抛异常。"""
    assert pcm_rms(_pcm(1000) + b"\x01") == pytest.approx(pcm_rms(_pcm(1000)))


def test_rms_is_monotonic():
    levels = [pcm_rms(_pcm(a)) for a in (0, 100, 1000, 10000, 30000)]
    assert levels == sorted(levels)


def test_duration_ms():
    pcm = _pcm(0, samples=16000)            # 16k 采样率下正好 1 秒
    assert pcm_duration_ms(pcm, 16000) == 1000
    assert pcm_duration_ms(b"", 16000) == 0


def test_wav_roundtrip():
    pcm = _pcm(1234, samples=800)
    data = pcm_to_wav_bytes(pcm, 16000)
    assert data[:4] == b"RIFF"
    back, err = wav_bytes_to_pcm(data, 16000)
    assert err is None and back == pcm


def test_wav_rejects_wrong_format():
    pcm = _pcm(0, samples=100)
    data = pcm_to_wav_bytes(pcm, 16000, channels=2)
    _back, err = wav_bytes_to_pcm(data, 16000)
    assert err and "格式不符" in err


def test_wav_rejects_garbage():
    _pcm2, err = wav_bytes_to_pcm(b"not a wav at all", 16000)
    assert err


def test_is_speech_threshold():
    assert is_speech(0.05, 0.02) is True
    assert is_speech(0.01, 0.02) is False
    assert is_speech(0.02, 0.02) is True       # 等于门限算说话
