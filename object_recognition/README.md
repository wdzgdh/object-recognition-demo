# 物体识别 Demo（选题一）

基于 ImageNet 预训练 ResNet18 与语音交互的物体识别 Demo。

## 功能

- **物体录入**：输入名称（键盘/语音），添加多张图片或摄像头抓拍，提取 ResNet18 特征并保存
- **物体识别**：本地图片或摄像头实时识别，输出最相似物体名 + 相似度
- **未知判断**：相似度低于阈值时判为"未知物体"（阈值界面可调）
- **语音交互**：语音说出物体名称、语音播报识别结果
- **特征库**：查看已录入物体、删除物体，重启后数据保留

## 环境要求

- Python 3.8+（本机已配置 `D:\anacoda` 环境，含 torch/cv2/PyQt5）
- 首次运行会自动下载 ResNet18 预训练权重（约 45MB）

## 安装依赖

```bash
# 核心依赖（本机 Anaconda 环境已装 torch/torchvision/opencv/PyQt5/numpy）
# 仅需补装语音相关：
pip install SpeechRecognition pyaudio pyttsx3
```

> **语音完全离线**：语音识别用 Windows 自带的 SAPI 中文识别引擎（需系统装有中文
> 语音识别器，中文 Windows 默认自带），语音合成用 pyttsx3(SAPI5)。均无需联网、无需 API key。
> `config.py` 里 `STT_BACKEND` 默认 `"sapi"`；改成 `"google"` 则优先尝试 Google 在线
> 识别（国内网络通常不可达，失败会自动降级回 sapi）。
> 若未装语音库，程序仍可运行，仅语音功能降级为键盘输入。

## 语音识别后端

`config.py` 的 `STT_BACKEND` 控制识别后端：

| 值 | 说明 | 协议 | 联网 | key |
|---|---|---|---|---|
| `"teleai"`（默认） | 天翼 AI 开放平台「实时语音识别(超多方言)」，支持热词 | WebSocket 流式 | 是 | 是 |
| `"baidu"` | 百度短语音识别，备用 | REST | 是 | 是 |
| `"google"` | Google 在线识别（国内通常不可达） | REST | 是 | 否 |
| `"sapi"` | Windows 自带中文离线识别 | 本地 COM | 否 | 否 |

**任何在线后端失败都会自动降级到离线 SAPI**，课堂演示断网也不会让语音功能整体失效。
状态栏显示当前后端，识别完成后标注本次实际使用的引擎。

### 🔐 密钥私密配置（重要）

**项目里没有任何明文密钥**，即使整个目录被提交也不会泄露。三层防护：

1. **位置隔离**：真实密钥存在用户目录
   `~/.object_recognition/credentials.bin`，**不在项目目录内**，git 碰不到。
2. **DPAPI 加密**：密文绑定当前 Windows 用户，换用户/换机器/复制到别处都解不开，
   密文本身也不含任何明文片段。
3. **`.gitignore`**：排除 `asr_config.json`、`*.bin`、`*.key`、`.env`、
   百度 token 缓存等；仓库里只提交 `asr_config.example.json`（空值模板）。

凭证读取优先级：**环境变量 > DPAPI 加密凭证 > 项目配置文件**。

管理凭证（脚本本身不含密钥）：

```bash
python setup_keys.py status    # 查看状态，只显示"已配置/未配置"，不显示内容
python setup_keys.py migrate   # 把配置文件里的明文密钥迁到加密存储并清空明文
python setup_keys.py set       # 交互式录入（输入不回显）
python setup_keys.py clear     # 删除本地加密凭证
```

也可用环境变量（优先级最高，适合演示/CI）：
`TELEAI_APP_ID` / `TELEAI_APPKEY` / `TELEAI_APIKEY`、
`BAIDU_ASR_API_KEY` / `BAIDU_ASR_SECRET_KEY`。

> **换电脑后**：clone 下来的代码没有你的密钥，程序会自动降级到离线 SAPI，
> 语音仍可用。在新机器上运行 `python setup_keys.py set` 重新录入即可。

### 天翼 ASR 接入说明（当前使用）

官方文档：https://www.teleai.com.cn/doc/QVY8etHoYdG4XoW4Nxvz/kFlF4Fsz7TH6VAmbBFIA

接入点 `wss://api-maas.teleai.com.cn/aipaas/voice/v1/realtimeAsrMultiZk/asr`，
Bearer Token 鉴权（`Authorization: Bearer <apikey>`，请求头带 `X-APP-ID`）。
三段式交互：`rec_status=0` 开始 → `rec_status=1` 按 200ms 节奏分块推送
base64(裸 PCM，16kHz/16bit/单声道) → `rec_status=2` 结束 → 收 `res_status=3/4` 的文本。

项目里的 `asr_config.json` **只放非敏感配置**（provider、热词），可安全提交：

```json
{
  "provider": "teleai",
  "teleai": { "app_id": "", "appkey": "", "apikey": "",
              "hotwords": ["水杯", "键盘", "保温杯"] },
  "baidu": { "api_key": "", "secret_key": "", "hotword": "" }
}
```

- `hotwords` 建议填你实际演示的物体名（<1000 个），可提升短词识别
- `appkey` 为「标准签名鉴权」预留，当前用 Bearer Token（用 `apikey`）
- 流式推送：2 秒语音约需 3 秒完成，状态栏会提示「约需数秒」

### 百度备用配置

如需切回百度，把 `STT_BACKEND` 改为 `"baidu"`，运行
`python setup_keys.py set` 录入百度密钥（或设环境变量）。
创建应用：https://console.bce.baidu.com/

## 语音使用提示

- **录入名称**：说完整句更准，例如「这是一个水杯」。程序会自动剥离「这是 / 一个 /
  录入」等口语前缀，只把核心词「水杯」填入名称输入框；识别有偏差可直接手动编辑。
- **语音播报**：可连续多次点击，每次都会播报（TTS 用独立工作线程 + 队列驱动）。
- 状态栏会显示明确的失败原因（无麦克风 / 超时没说话 / 没听清 / 引擎不可用）。

## 运行

```bash
cd object_recognition
python main.py
```

> 若使用 Anaconda 环境：`/d/anacoda/python.exe main.py`

## 项目结构

```
object_recognition/
├── main.py          # 程序入口
├── config.py        # 配置（阈值/路径/设备等）
├── feature.py       # ResNet18 特征提取（512维）
├── library.py       # 特征库持久化（JSON + npy）
├── recognize.py     # 余弦相似度检索识别
├── speech.py        # 语音识别(STT) + 语音合成(TTS)
├── asr_teleai.py    # 天翼实时语音识别适配器（WebSocket 流式，默认）
├── asr_baidu.py     # 百度短语音识别适配器（备用）
├── credentials.py   # DPAPI 加密凭证存取（凭证在项目目录之外）
├── setup_keys.py    # 凭证管理工具（迁移/查看/设置/清除）
├── asr_config.json  # 仅非敏感配置（provider、热词），可提交
├── asr_config.example.json  # 空值模板
├── ui/app.py        # PyQt 主界面（识别/录入/特征库三 Tab）
├── utils/camera.py  # 摄像头采集线程
└── requirements.txt
```

## 🔄 换设备时特征库不丢失（三种方式）

特征库存两个文件：`data/library.json`（物体名/时间）+ `data/library_feats.npy`
（512 维特征）。它们**不含任何密钥**（密钥已按上文加密存到用户目录）。

| 方式 | 做法 | 适合 |
|---|---|---|
| **① 随代码提交（默认）** | 两个文件已不再被 `.gitignore` 排除，`git commit` + 新设备 `git clone` 即自动带上 | 用 git 管理项目 |
| **② 一键导出/导入** | 特征库 Tab →「导出备份」生成单个 zip → 拷到新设备 →「导入备份」 | U 盘拷贝、发给组员/老师 |
| **③ 云盘自动同步** | 设环境变量 `OBJECT_REC_LIBRARY_DIR` 指向 OneDrive 等同步目录 | 多设备长期自动同步 |

- 导入时可选择「覆盖」（换设备恢复）或「合并」（合并两台设备的数据）。
- 导入前会校验文件完整性和特征维度，**校验失败不会破坏现有特征库**。
- 方式③ 只把特征库放到云盘；百度 token 缓存仍留在项目本地目录，不会把密钥同步上云。
  示例：`set OBJECT_REC_LIBRARY_DIR=D:\OneDrive\object_rec_library`

> 注意：换设备后**密钥不会跟着走**（DPAPI 绑定原 Windows 用户），
> 程序会自动降级到离线 SAPI；在新设备运行 `python setup_keys.py set` 重新录入即可。

## 📱 使用手机摄像头（替代电脑摄像头）

识别 Tab 新增「摄像头源」区域：

| 模式 | 行为 |
|---|---|
| **自动（优先手机）**（默认） | 手机连上就用手机，没连/断流自动回退本地摄像头 |
| 仅本地摄像头 | 只用电脑摄像头 |
| 仅手机摄像头 | 只用手机，连不上就报错 |

「手机地址」填任意 MJPEG / RTSP 流 URL，改动会自动保存到
`camera_config.json`（不含密钥，可随代码提交）。也可用环境变量
`OBJECT_REC_PHONE_CAMERA_URL` 临时覆盖。

### 方式 A：USB 连接（默认地址，不依赖 WiFi）

1. 手机装 **IP Webcam**（Android），开启「开发者选项 → USB 调试」，用数据线连接电脑
   （首次连接需在手机上点「允许 USB 调试」）
2. 双击项目根目录的 `连接手机摄像头.bat`
   —— 脚本会自动找到本机 adb（已装在
   `%LOCALAPPDATA%\Android\Sdk\platform-tools\`）并执行 `adb forward tcp:8080 tcp:8080`；
   若换电脑没装 adb，需安装 Android Platform Tools，或改用下面的 WiFi 方式
3. 在 IP Webcam 里点「Start server」
4. 程序里保持默认地址 `http://127.0.0.1:8080/video`，点「开启摄像头实时识别」

USB 转发把手机 8080 端口映射到电脑本机，因此地址固定为 `127.0.0.1:8080`，
教室/宿舍换网络也不用改。

### 方式 B：同一 WiFi（无需数据线）

1. 手机装 IP Webcam，Start server，记下屏幕上显示的地址（如 `http://192.168.1.23:8080`）
2. 程序「手机地址」填 `http://192.168.1.23:8080/video`
3. 点「开启摄像头实时识别」

### 常见 App 的流地址

| 方案 | 地址格式 |
|---|---|
| IP Webcam (Android) | `http://IP:8080/video` |
| DroidCam (Android/iOS) | `http://IP:4747/video` |
| 通用 RTSP 摄像头 App | `rtsp://user:pass@IP:port/path` |
| EpocCam / Camo (iOS) | 装 PC 客户端后注册为系统摄像头 → 用「仅本地」模式并调 `CAMERA_INDEX` |

> iPhone 多数方案走 PC 客户端虚拟摄像头（不是 URL）。若客户端把手机注册成
> 系统摄像头，把 `config.CAMERA_INDEX` 改成对应索引，并选「仅本地摄像头」即可。

### 故障排查

- 状态栏显示「未检测到手机摄像头，使用本地摄像头」→ 地址不通，检查 USB 转发 / 同一 WiFi / App 是否已 Start server
- 画面卡顿 → 手机端把分辨率调到 640×480 或更低；程序已把缓冲设为 1 帧
- 断流 → 采集线程会自动重连 3 次，仍失败会提示重新开启
- 只想用电脑摄像头 → 模式切到「仅本地摄像头」

## 数据准备（课程要求）

- 至少 5 个物体，每个 ≥5 张图片
- 注册图与测试图不重复
- 至少 3 个未录入物体用于测试"未知物体"
