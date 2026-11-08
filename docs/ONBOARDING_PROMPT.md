# 加入协作：三条可直接复制的消息

仓库已公开：**https://github.com/wdzgdh/object-recognition-demo**

下面三段按顺序用：
1. 发给对方本人（微信/QQ 直接粘贴）
2. 等对方接受邀请后，把「加入包」发给他
3. 让对方把「开工提示词」整段粘给他的 AI 助手（WorkBuddy / Claude / Cursor 都行）

---

## 1. 发给对方本人的话

> 课程大作业那个物体识别 Demo，我把它整理成仓库开源了：
> https://github.com/wdzgdh/object-recognition-demo
>
> 里面不只是代码，还有一份缺陷评审和 12 个拆好的任务，分 A/B 两条线，几乎不用等对方。
> 你负责 B 线（算法、语音、数据），我走 A 线（界面、可视化）。
> 现在把 12 个 issue 全建好了，从 wave:1 开始领：#7 #8 #9。
>
> 你先点这个链接加协作者：https://github.com/wdzgdh/object-recognition-demo/invitations
> 加完 clone 下来跑一遍 `python main.py` 能起界面，就说明环境通了。

---

## 2. 加入包（对方接受邀请后发）

> **仓库**：https://github.com/wdzgdh/object-recognition-demo （已开源，MIT）
>
> **拉代码**
> ```
> git clone https://github.com/wdzgdh/object-recognition-demo.git
> cd object-recognition-demo/object_recognition
> pip install -r requirements.txt
> python main.py
> ```
> 首次运行会下 ResNet18 权重（45 MB）。Windows 也可直接双击根目录 `启动物体识别Demo.bat`。
>
> **必读三份（30 分钟，别跳）**
> - `docs/PLAN.md` —— 12 个任务、验收指标、4 个波次
> - `docs/DIVISION.md` —— 谁改哪些文件（有所有权表）、怎么开分支
> - `docs/CONTRACT.md` —— 跨人调用的函数签名，签名先定再写实现
>
> **你的范围**：`feature.py` `recognize.py` `library.py` `speech.py` `asr_*.py` `utils/` `config.py` `tools/` `tests/`
> **别碰**：`ui/` `main.py`（那是 A 线）、`data/`（二进制冲突，只由我提交）
>
> **分支**：`git switch -c feat/b1-launcher`，一个 issue 一个分支一个 PR，PR ≤ 400 行。
> **评审**：我的 PR 你 approve，你的 PR 我 approve，自己不能合自己的。
>
> 从这几个开始（都在 wave:1，互相不依赖，随便挑顺序）：
> - #7 启动器与配置治理（2 h，最容易上手）
> - #8 特征库健壮性（4 h，纯逻辑，最能体现功底）
> - #9 语音基础设施（6 h，最有技术含量）

---

## 3. 开工提示词（让对方整段复制给他的 AI 助手）

```text
你是我的结对开发伙伴，我们要一起改一个已开源的 Python 桌面项目。请先读完下面的事实，再开始动手，不要凭印象猜。

【项目】
仓库：https://github.com/wdzgdh/object-recognition-demo （MIT，公开）
内容：课程大作业「交互式物体识别 Demo」——ImageNet 预训练 ResNet18 提特征（512 维，L2 归一化），
余弦相似度做特征检索，低于阈值判「未知物体」，PySide6 写界面，语音负责录入名称与播报结果。
主程序在 object_recognition/，入口 main.py，本地跑法：
  cd object_recognition && pip install -r requirements.txt && python main.py

【背景：已经做过一次全量代码评审】
仓库根目录 代码评审_缺陷分析.md 里有完整的 P0/P1 缺陷清单（带文件行号）。已确认的严重问题包括：
- 语音是假闭环：识别 Tab 的「语音识别」只把用户原话复述一遍（ui/app.py:502-504），不触发识别
- 实时推理跑在 GUI 主线程（ui/app.py:434-438），每 300 ms 阻塞 40–180 ms
- 特征库加载不校验、写盘非原子（library.py:35-52），items 与 npy 行数不一致会让 recognize.py:64 下标越界
- 语音链路是「假流式」：先录完整段 WAV 再按实时速率 sleep 上传（asr_teleai.py:147-153），2 s 语音要 5 s
- 启动脚本硬编码 D:\anacoda\python.exe
不要重新做一遍评审，直接按仓库里已经拆好的任务干。

【我已确认的分工】
我负责 B 线：算法、语音、数据、基础设施。
文件范围（只能改这些）：feature.py、recognize.py、library.py、speech.py、asr_baidu.py、asr_teleai.py、
utils/、config.py，以及新建 tools/、tests/、infer_worker.py。
绝对不要改：ui/ 目录下的任何文件、main.py（属 A 线）、data/ 下的二进制数据（二进制冲突，只由 A 提交）。
如果某个改动必须动 A 的文件，停下来告诉我，我去和对方对齐，不要自己动手改。

【必须先读的三份文档（按顺序）】
1. docs/PLAN.md —— 12 个任务 A1–A6 / B1–B6、验收指标、4 个波次
2. docs/DIVISION.md —— 文件所有权表、分支模型、冲突预案
3. docs/CONTRACT.md —— A/B 接口契约。这是硬约束：
   - 新增 infer_worker.py 的 InferWorker，签名必须严格照第 1 节（result / latency / fps / error 四个信号，
     start / submit / set_threshold / stop 四个方法）
   - Speech 的扩展方法必须照第 2 节（on_level / on_partial / cancel_event / cancel / speak_result）
   - CameraThread 新增 fps / dropped 属性，且 frame_bgr 语义改为「可能丢帧」
   - FeatureLibrary 新增 validate / stats / rename / delete_one / thumb_paths
   - 契约要改，先单独提 PR 改 docs/CONTRACT.md，再改代码

【工作方式（必须遵守）】
1. 一个 issue = 一个分支 = 一个 PR：git switch -c feat/b4-infer-worker
2. 分支每天 rebase origin/main：git fetch && git rebase origin/main（不要 merge main 进 feature）
3. 提交信息：<type>(<scope>): <中文简述>，scope 取 core|speech|library|camera|tools|docs
4. PR 用 .github/PULL_REQUEST_TEMPLATE.md 的清单，逐条自测后打勾，关联 issue 写 Closes #10
5. 不用自己合并；我 approve 后 squash merge
6. 纯逻辑必须能在无 torch 环境测（CI 不装 torch，只跑 ruff check + compileall + pytest）：
   检索打分、评测指标这类写成 tests/ 下的纯函数单测
7. 不要顺手重构无关代码，不要改别人文件，不要新增 print 调试输出

【本次请从这三个 wave:1 任务里挑一个开始，先只做这一个】
- #7 [B1] 启动器与配置治理（2 h）：bat 自适应探测 python / py -3 / conda 路径 + chcp 65001；
  config.py 新增 AUTO_SPEAK / HISTORY_MAX / SHOW_OVERLAY / ROI_RATIO / SMOOTH_N / INFER_INTERVAL_MS / THRESHOLD_STORE；
  阈值滑条改动落盘到 data/ui_state.json
- #8 [B2] 特征库健壮性（4 h）：load() 校验 items 与 feats 行数、维度，不一致截断并在 UI 顶部黄条提示；
  _save() 改 tmp + os.replace 原子写；单实例文件锁；实现契约里的 validate/stats/rename/delete_one/thumb_paths；
  录入时把原图副本存 data/thumbs/<name>/
- #9 [B3] 语音基础设施（6 h）：cancel_event + cancel() 支持中断；边录边推让 2 s 语音端到端 ≤ 3 s；
  on_level 音量回调（约 20 Hz）；on_partial 回传云端增量文本；TTS 引擎只 init 一次、队列去重可打断

开始之前：先把仓库 clone 下来，读那三份文档和相关源码（feature.py / library.py / speech.py / asr_teleai.py），
然后用 5 行以内告诉我你选的 issue 编号、你打算改哪几个函数、以及有没有需要和 A 线对齐的地方。
不要一上来就写代码。
```

---

## 附：如果对方更擅长界面，把 A/B 反过来

只需交换两处：

- 文件范围换成 `ui/` + `main.py`（A 线），别碰 core / speech / tools
- 任务换成 A1–A6（#1–#6）

契约本身是对称的：接口签名不变，只是由谁实现换了个人。汇合点也跟着换——原本 A4 等 B4，现在变成「做 B4 的人先合，做 A4 的人再拉」。

唯一不能换的是那条铁律：**`data/` 只由一个人提交**，否则 npy 二进制冲突会吃掉半天。
