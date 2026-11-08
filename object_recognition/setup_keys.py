# -*- coding: utf-8 -*-
"""凭证管理工具（本文件不含任何密钥）。

用法：
    python setup_keys.py migrate   从 asr_config.json 迁移明文密钥 → 加密存储，并清空明文
    python setup_keys.py status    查看凭证配置状态（只显示"已配置/未配置"，不显示内容）
    python setup_keys.py set       交互式录入密钥（输入不回显）
    python setup_keys.py clear     删除本地加密凭证

密钥最终存放在用户目录 ~/.object_recognition/credentials.bin（DPAPI 加密），
不在项目目录内，不会被 git 跟踪。
"""
import getpass
import json
import os
import sys

import credentials

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "asr_config.json")

SECRET_FIELDS = {
    "teleai": ["app_id", "appkey", "apikey"],
    "baidu": ["api_key", "secret_key"],
}


def _read_json(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _mask(value):
    if not value:
        return "未配置"
    v = str(value)
    return "已配置 (%s…%s)" % (v[:3], v[-4:]) if len(v) > 9 else "已配置"


def cmd_status():
    print("DPAPI 支持:", "是" if credentials.available() else "否（请用环境变量）")
    print("凭证文件:", credentials.path())
    print("文件存在:", os.path.exists(credentials.path()))
    enc = credentials.load() or {}
    cfg = _read_json(CONFIG_PATH)
    print()
    for section, fields in SECRET_FIELDS.items():
        print("[%s]" % section)
        for k in fields:
            val = (enc.get(section) or {}).get(k) or (cfg.get(section) or {}).get(k)
            env_name = {
                ("teleai", "app_id"): "TELEAI_APP_ID",
                ("teleai", "appkey"): "TELEAI_APPKEY",
                ("teleai", "apikey"): "TELEAI_APIKEY",
                ("baidu", "api_key"): "BAIDU_ASR_API_KEY",
                ("baidu", "secret_key"): "BAIDU_ASR_SECRET_KEY",
            }.get((section, k))
            env_val = os.environ.get(env_name, "") if env_name else ""
            src = "环境变量" if env_val else ("加密存储" if (enc.get(section) or {}).get(k)
                                        else ("明文配置(建议迁移)" if (cfg.get(section) or {}).get(k)
                                              else "无"))
            print("  %-12s %-22s 来源: %s" % (k, _mask(val), src))
    print()
    print("注意：状态命令不会显示密钥内容。")


def cmd_migrate():
    cfg = _read_json(CONFIG_PATH)
    secrets = {}
    for section, fields in SECRET_FIELDS.items():
        for k in fields:
            v = (cfg.get(section) or {}).get(k)
            if v:
                secrets.setdefault(section, {})[k] = v
    if not any(secrets.values()):
        print("asr_config.json 中没有明文密钥，无需迁移。")
        return

    ok, err = credentials.save(secrets)
    if not ok:
        print("加密保存失败:", err)
        sys.exit(1)

    # 校验：解密回来的内容必须与原文一致
    back = credentials.load() or {}
    for section, fields in SECRET_FIELDS.items():
        for k in fields:
            if (cfg.get(section) or {}).get(k):
                if back.get(section, {}).get(k) != cfg[section][k]:
                    print("校验失败：解密结果与原文不一致，已保留明文配置。")
                    credentials.clear()
                    sys.exit(1)

    # 明文配置里清空密钥字段，只保留非敏感项（provider / hotwords）
    for section, fields in SECRET_FIELDS.items():
        if section in cfg:
            for k in fields:
                cfg[section][k] = ""
    _write_json(CONFIG_PATH, cfg)
    print("迁移完成 ✅")
    print("  密钥已加密保存到:", credentials.path())
    print("  asr_config.json 中的密钥字段已清空（现在可安全提交）")


def cmd_set():
    print("交互式设置凭证（输入不回显，直接回车跳过保持原值）")
    enc = credentials.load() or {}
    for section, fields in SECRET_FIELDS.items():
        print("\n[%s]" % section)
        enc.setdefault(section, {})
        for k in fields:
            v = getpass.getpass("  %s: " % k)
            if v.strip():
                enc[section][k] = v.strip()
    ok, err = credentials.save(enc)
    if not ok:
        print("保存失败:", err)
        sys.exit(1)
    print("\n已加密保存到:", credentials.path())


def cmd_clear():
    if credentials.clear():
        print("已删除本地加密凭证。程序将回退到环境变量或离线 SAPI。")
    else:
        print("删除失败。")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    cmds = {
        "status": cmd_status, "migrate": cmd_migrate,
        "set": cmd_set, "clear": cmd_clear,
    }
    if cmd not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[cmd]()


if __name__ == "__main__":
    main()
