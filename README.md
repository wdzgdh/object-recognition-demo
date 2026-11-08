# 交互式物体识别 Demo（选题一）

基于 ImageNet 预训练 **ResNet18** 特征提取 + **余弦相似度检索** + **语音交互** 的课程大作业。系统不认识任何自定义物体，通过"展示物体 + 说出名称"让它当场学会，再以语音播报识别结果；相似度低于阈值时判为未知物体。

## 效果一览

| 功能 | 说明 |
|---|---|
| 物体录入 | 键盘/语音输入名称，本地图片或摄像头抓拍，多张图各提一条 512 维特征 |
| 物体识别 | 图片或摄像头实时识别，输出名称 + 相似度 + Top-3 候选 |
| 未知判断 | 相似度低于阈值（界面可调）判为"未知物体" |
| 语音交互 | 语音说出名称、语音播报结果，在线 ASR 失败自动降级离线 SAPI |
| 特征库 | 增删查、缩略图、跨设备 zip 导出/导入、退出重启不丢 |

## 快速开始

```bash
git clone <this-repo>
cd object_recognition
pip install -r requirements.txt
python main.py
```

Windows 也可直接双击根目录的 `启动物体识别Demo.bat`（自动探测 Python 解释器）。

> 首次运行会下载 ResNet18 权重（约 45 MB）。手机摄像头接入方式见 [object_recognition/README.md](object_recognition/README.md)。

## 文档索引

| 文档 | 内容 |
|---|---|
| [选题一_需求规格说明书.md](选题一_物体识别Demo_需求规格说明书.md) | 需求、评分标准、验收清单 |
| [代码评审_缺陷分析.md](代码评审_缺陷分析.md) | 缺陷基线与证据（P0/P1、文件行号） |
| [docs/PLAN.md](docs/PLAN.md) | 缺陷治理方案：12 个任务、验收指标、时间表 |
| [docs/DIVISION.md](docs/DIVISION.md) | 两人分工、文件所有权、分支模型、并行规则 |
| [docs/CONTRACT.md](docs/CONTRACT.md) | A/B 接口契约（签名先定，再各自实现） |
| [docs/ONBOARDING_PROMPT.md](docs/ONBOARDING_PROMPT.md) | 新人加入的三段话 + 可直接粘给 AI 助手的开工提示词 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 如何提 issue / PR、自测清单、CI 说明 |

## 仓库结构

```
.
├── object_recognition/          # 主程序
│   ├── main.py                  # 入口（启动 GUI）
│   ├── config.py                # 阈值 / 路径 / 设备 / 语音后端
│   ├── feature.py               # ResNet18 特征提取（512 维，L2 归一化）
│   ├── recognize.py             # 余弦相似度检索 + 未知判断
│   ├── library.py               # 特征库持久化（JSON + npy）、zip 备份迁移
│   ├── speech.py                # STT + TTS（常驻线程、失败降级）
│   ├── asr_teleai.py            # 天翼实时语音识别（WebSocket）
│   ├── asr_baidu.py             # 百度短语音识别（备用）
│   ├── credentials.py           # DPAPI 加密凭证（存用户目录）
│   ├── ui/app.py                # PySide6 主界面（识别 / 录入 / 特征库）
│   ├── utils/camera.py          # 摄像头采集线程（本地 + 手机流）
│   └── data/                    # 特征库数据
├── data/                        # 课程要求的数据集目录（register / test / unknown）
├── docs/                        # 方案、分工、契约
└── image/                       # 演示用图片
```

## 协作方式

需要拉人进来？照 [docs/ONBOARDING_PROMPT.md](docs/ONBOARDING_PROMPT.md) 走，里面有三段可直接复制的消息（给本人 / 加入包 / 给对方的 AI 助手）。

- **任务看板**：[Issues #1–#12](https://github.com/wdzgdh/object-recognition-demo/issues)，每个任务一个 issue，编号 A1–A6 / B1–B6
- 主干 `main` 只接收 PR，禁止直接 push
- 一个 issue = 一个分支 = 一个 PR（≤ 400 行）
- 交叉评审：A 的 PR 由 B approve，B 的 PR 由 A approve
- 文件所有权见 [docs/DIVISION.md](docs/DIVISION.md#2-文件所有权表唯一权威)

| 标签 | 含义 |
|---|---|
| `owner:A` / `owner:B` | 负责人（界面可视化 / 算法语音数据） |
| `wave:1` `wave:2` `wave:3` | 波次，同波次内可完全并行 |
| `P0` / `P1` / `P2` | 严重度：演示会挂 / 影响体验 / 观感 |
| `contract` | 涉及跨人接口，改动需先改 `docs/CONTRACT.md` |

## 合规与安全

- 项目内**不含任何明文密钥**：真实凭证用 Windows DPAPI 加密后存于用户目录 `~/.object_recognition/credentials.bin`，不在仓库内
- 换机器后程序自动降级离线语音，运行 `python object_recognition/setup_keys.py set` 重新录入即可
- 仅使用公开预训练权重与自采集图片，不伪造结果

## 许可

本项目以 [MIT License](LICENSE) 开源，欢迎 fork、提 issue 与 PR。

第三方组件声明：

- ResNet18 预训练权重由 torchvision 提供，版权归 PyTorch 与 ImageNet 原作者，遵循其各自许可（BSD-3-Clause / ImageNet Terms）
- PySide6 / PyQt5、OpenCV、SpeechRecognition、pyttsx3 等依赖遵循各自许可证
- 本项目仅使用公开预训练权重与自采集图片，不伪造实验结果
