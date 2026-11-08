# -*- coding: utf-8 -*-
"""本地凭证加密存储（Windows DPAPI）。

安全模型：
- 真实密钥**不放在项目目录**，而是存到用户目录
  `~/.object_recognition/credentials.bin`，git 无法触及。
- 内容用 **Windows DPAPI** 加密，密文绑定「当前 Windows 用户」：
  换用户、换机器、复制到别处都无法解密。即使文件被误提交，别人拿到的
  也只是无法解开的密文。
- 非 Windows 平台没有 DPAPI 时自动返回 None，程序改用环境变量或
  离线 SAPI，不会以明文兜底。

优先级（由 config.py 决定）：环境变量 > 本模块密文 > 配置文件中的非敏感项。
"""
import json
import os

APP_DIR = os.path.join(os.path.expanduser("~"), ".object_recognition")
CRED_FILE = os.path.join(APP_DIR, "credentials.bin")
_DESCRIPTION = "object_recognition_asr_v1"


def available():
    """当前平台是否支持 DPAPI 加解密。"""
    try:
        import win32crypt  # noqa: F401
        return True
    except Exception:
        return False


def save(secrets):
    """把凭证 dict 加密落盘。返回 (ok, error_message)。"""
    if not available():
        return False, "当前平台不支持 DPAPI，请改用环境变量"
    try:
        import win32crypt
        raw = json.dumps(secrets, ensure_ascii=False).encode("utf-8")
        blob = win32crypt.CryptProtectData(
            raw, _DESCRIPTION, None, None, None, 0)
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = CRED_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, CRED_FILE)
        try:
            os.chmod(CRED_FILE, 0o600)
        except Exception:
            pass
        return True, None
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def load():
    """读取并解密凭证。失败/不存在返回 None（不抛异常、不回退明文）。"""
    if not os.path.exists(CRED_FILE) or not available():
        return None
    try:
        import win32crypt
        with open(CRED_FILE, "rb") as f:
            blob = f.read()
        raw = win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def clear():
    """删除本地加密凭证。"""
    try:
        if os.path.exists(CRED_FILE):
            os.remove(CRED_FILE)
        return True
    except Exception:
        return False


def path():
    return CRED_FILE
