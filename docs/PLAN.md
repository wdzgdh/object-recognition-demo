# 缺陷治理方案（v1）

> 配套文档：[两人分工与并行规则](DIVISION.md) · [接口契约](CONTRACT.md)
> 缺陷基线：[../代码评审_缺陷分析.md](../代码评审_缺陷分析.md)
> 适用：2 人并行、约 4–5 个工作日（含 1 天联调）

---

## 1. 目标

不是"补几个功能"，而是把三个评分项从"看不出来"变成"看得见、说得清、测得到"。

| 目标 | 当前状态 | 交付状态 | 衡量方式 |
|---|---|---|---|
| 语音真正参与识别 | 点"语音识别"只复述用户原话 | 说"这是什么"即触发识别，识别完自动播报 | 现场演示 3 次，无手动点播报按钮 |
| 界面不卡 | 推理在 GUI 线程，每 300 ms 阻塞 40–180 ms | 主线程单次阻塞 ≤ 16 ms | 叠层显示 FPS / 推理 ms，连续 60 s 无掉帧 |
| 可视化可解释 | 整帧图 + 一行文字 | Top-3 条形图、结果历史、缩略图、未知徽标、指标叠层 | 截图不少于 6 张，覆盖清单全部条目 |
| 数据说话 | 只有 2 个物体、目录全空、无评测 | 5 物体 × 5 图 + 3 未知；一条命令出 Top-1/未知召回/混淆矩阵 | `python tools/evaluate.py` 输出 CSV |
| 一处启动 | bat 硬编码 `D:\anacoda`，本机不存在 | 双击即启动，缺依赖有中文提示 | 新机器 clone 后双击可跑 |

**验收指标（写进报告的数字）**

- 图片识别 Top-1 ≥ 90%（5 类，测试集与注册集不重叠）
- 未知物体召回 ≥ 80%，已知物误判为未知 ≤ 20%
- 语音端到端（2 s 语句）≤ 3 s；点击到出结果无"假死"
- 实时识别输出频率 ≥ 3 Hz，预览 ≥ 25 FPS
- 启动到可交互 ≤ 3 s（权重已缓存）；无窗口黑屏期

---

## 2. 改造方向（架构层，两句话）

1. **把"算"从界面线程里赶出去**：新增 `InferWorker`（工作线程），UI 只负责显示结果与叠层；相机线程只保留最新帧，丢弃积压。
2. **把"说"和"看"接起来**：语音文本进 `_on_stt_result` 后先做意图分流（识别 / 录入），识别结果回灌给 TTS 播报，形成闭环。

其余缺陷（可视化、健壮性、评测）都是在这两条主线上挂载。

---

## 3. 任务清单

12 个任务，**A/B 各 6 个，10 个完全独立，2 个汇合点**。任务粒度按"一个 PR 能讲完"控制（≤ 400 行 diff）。

### 3.1 A 线：界面 / 可视化 / 交互（负责人 A）

| ID | 任务 | 涉及文件 | 依赖 | 验收标准 | 预估 |
|---|---|---|---|---|---|
| **A1** | 语音闭环 | `ui/app.py` | 无 | ①识别成功后自动播报"这是 XX"/"我不认识"；②说"这是什么/识别一下"触发当前帧识别；③说"录入"切到录入 Tab；④"自动播报"可勾选关闭 | 3 h |
| **A2** | 结果可视化 | 新增 `ui/widgets.py`，改 `app.py` | 无 | Top-3 用横向条形图（长度按分数、含百分比）；未知时显示灰色徽标「未知」+ 图标；状态不再只靠颜色 | 4 h |
| **A3** | 缩略图与库管理 | 新增 `ui/gallery.py`，改 `app.py` | 无 | 录入页显示缩略图网格（可单张移除）；特征库显示缩略图 + 录入时间 + 张数，支持重命名、单张删除 | 4 h |
| **A4** | 指标叠层 + 结果历史 | 新增 `ui/history.py`，改 `app.py` | **B4** | 预览左上角叠 FPS / 推理 ms / 结果；右侧历史列表保留最近 20 条（时间、结果、分数、来源），可清空、可点击回看 | 5 h |
| **A5** | 启动与演示体验 | `main.py`，改 `app.py` | 无 | 启动闪屏 + "正在加载模型…"；模型加载失败弹中文提示而非崩溃；全屏演示模式（F11）；快捷键（空格=识别、R=录入） | 4 h |
| **A6** | 演示脚本与截图 | `docs/demo.md`，`docs/img/` | A1–A5 | 一页纸演示流程（5 步，含备用方案）；6 张截图入报告 | 3 h |

### 3.2 B 线：核心 / 语音 / 数据 / 基础设施（负责人 B）

| ID | 任务 | 涉及文件 | 依赖 | 验收标准 | 预估 |
|---|---|---|---|---|---|
| **B1** | 启动器与配置治理 | `启动物体识别Demo.bat`，`config.py` | 无 | bat 依次探测 `python` / `py -3` / 常见 conda 路径，失败给中文提示；`chcp 65001`；阈值滑条改动落盘（重启保留）；新增配置键（见契约） | 2 h |
| **B2** | 特征库健壮性 | `library.py`，`recognize.py` | 无 | 加载时校验 items/feats 数量与维度，不一致截断并告警；写盘改 `tmp` + `os.replace` 原子替换；单实例文件锁（第二实例给提示）；人为改坏 npy 不再崩溃 | 4 h |
| **B3** | 语音基础设施 | `speech.py`，`asr_teleai.py`，`asr_baidu.py` | 无 | ①聆听中可"停止"（cancel_event）；②边录边推，2 s 语句端到端 ≤ 3 s；③`on_level` 实时音量回调；④`on_partial` 中间结果回调；⑤TTS 引擎复用，不重复 init，队列去重可打断 | 6 h |
| **B4** | 推理线程化 | 新增 `infer_worker.py`，`utils/camera.py`，`feature.py` | 无 | 推理在独立线程，UI 主线程单次阻塞 ≤ 16 ms；相机线程只保留最新帧；预览缩放改 `FastTransformation`；空转时 CPU 占用 < 15% | 5 h |
| **B5** | 识别质量 | `feature.py`，`recognize.py`，`utils/image.py`(新) | 无 | EXIF 方向修正；中心 ROI 可配（`ROI_RATIO`）；实时结果连续 N 帧一致才上屏；阈值与聚合分口径统一（界面与判定用同一分数） | 5 h |
| **B6** | 批量工具与评测 | 新增 `tools/batch_register.py`、`tools/evaluate.py`、`tests/` | B2 | 一条命令把 `data/register/*/` 批量入库；`evaluate.py` 输出 Top-1、Top-3、未知召回、误接受率 + 混淆矩阵 CSV；纯函数单测可在无 torch 环境跑 | 6 h |

### 3.3 汇合点（唯一的两处，必须按顺序）

```
B4 ──► A4        B2 ──► A3（软依赖，可用占位实现先合）
```

- **A4 依赖 B4**：A4 需要 `InferWorker.result/latency` 信号。B4 先合，A4 再拉最新 main。
- **A3 软依赖 B2**：缩略图需要 `thumb_paths()`。B2 未合时 A3 用「名称首字」占位块，不阻塞。
- B3 是可选增强：A1 用现有 one-shot STT 即可完成闭环，不阻塞。

### 3.4 Issue 索引（GitHub 看板）

| 任务 | Issue | 任务 | Issue |
|---|---|---|---|
| A1 语音闭环 | [#1](https://github.com/wdzgdh/object-recognition-demo/issues/1) | B1 启动器与配置 | [#7](https://github.com/wdzgdh/object-recognition-demo/issues/7) |
| A2 结果可视化 | [#2](https://github.com/wdzgdh/object-recognition-demo/issues/2) | B2 特征库健壮性 | [#8](https://github.com/wdzgdh/object-recognition-demo/issues/8) |
| A3 缩略图 | [#3](https://github.com/wdzgdh/object-recognition-demo/issues/3) | B3 语音基础设施 | [#9](https://github.com/wdzgdh/object-recognition-demo/issues/9) |
| A4 叠层与历史 | [#4](https://github.com/wdzgdh/object-recognition-demo/issues/4) | B4 推理线程化 | [#10](https://github.com/wdzgdh/object-recognition-demo/issues/10) |
| A5 启动体验 | [#5](https://github.com/wdzgdh/object-recognition-demo/issues/5) | B5 识别质量 | [#11](https://github.com/wdzgdh/object-recognition-demo/issues/11) |
| A6 演示与截图 | [#6](https://github.com/wdzgdh/object-recognition-demo/issues/6) | B6 批量与评测 | [#12](https://github.com/wdzgdh/object-recognition-demo/issues/12) |

---

## 4. 时间表（4 个波次）

| 波次 | 时长 | A 线 | B 线 | 合并门禁 |
|---|---|---|---|---|
| **W0 基座** | 已完成 | — | — | 文档、模板、契约已入库 |
| **W1** | 1.5 天 | A1 → A2 → A3 | B1 → B2 → B3 | 每个 PR 单独可跑，不改对方文件 |
| **W2** | 2 天 | A4（待 B4）→ A5 | B4 → B5 | B4 合入当天在 #10 贴接线示例并 @A |
| **W3** | 1 天 | A6 + 联调 | B6 + 联调 | 全部指标复测，出评测 CSV |
| **W4** | 0.5 天 | 报告数据回填 | 报告截图/表格 | 报告定稿 |

每日节奏：早上在 issue 里写"今日目标"，晚上推一个 PR（哪怕小），**不做跨天的大 PR**。

---

## 5. 风险与对策

| 风险 | 触发场景 | 对策 |
|---|---|---|
| `data/` 二进制冲突 | 两人各自录入不同物体，同时改 `library.json` / `.npy` | 约定 **`data/` 只由 A 提交**；B 的 PR 不得包含 `data/`。已加 `.gitattributes` 标 binary |
| 两人都动 `ui/app.py` | B 想顺手改 UI | **文件所有权表**（见 DIVISION.md）：`ui/` 归 A，B 只新增模块，不改 A 的文件 |
| 录音环境差异 | 一人有麦克风的机器，一人没有 | B3 用 `cancel_event` + 假 WAV 单元测试，不依赖真实麦克风 |
| 无 torch 机器跑不起来 | 评审机上无 CUDA/无 torch | 纯函数（检索、意图、评测指标）必须无 torch 可测；CI 只跑 ruff + compileall |
| 模型权重首次下载失败 | 教室断网 | A5 加中文错误提示 + README 说明手动放权重路径 |
| 数据不达标（只有 2 个物体） | 演示前发现 | 列为 **W1 前置**：先补 5 物体 × 5 图 + 3 未知物，再谈准确率 |

---

## 6. 验收清单（W3 逐条打勾）

- [ ] 说"这是什么"→ 自动识别 → 自动播报名称
- [ ] 未知物体 → 界面灰色徽标 + 播报"我不认识"
- [ ] 摄像头连续 60 s：FPS ≥ 25、推理 ms 可见、无卡顿
- [ ] Top-3 条形图随画面变化，分数差距可见
- [ ] 录入 5 张图后能看到 5 张缩略图
- [ ] 特征库能重命名、能删单张
- [ ] 双击 bat 在新机器启动成功
- [ ] 人为破坏 npy → 程序提示而非崩溃
- [ ] `tools/evaluate.py` 输出 CSV，数字回填报告
- [ ] 语音点"停止"能立刻中断聆听

---

## 7. 非目标（明确不做，避免范围膨胀）

- 不重训/微调模型（课程要求讲的是特征检索）
- 不做 Web/App 端，不做服务器部署
- 不引入 YOLO 等检测模型做目标框（Grad-CAM 在"加分"里，不占主线）
- 不做多用户/权限体系
