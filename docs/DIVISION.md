# 两人分工与并行规则

> 配套：[缺陷治理方案](PLAN.md) · [接口契约](CONTRACT.md)

## 1. 角色

| | **A：体验与可视化** | **B：算法、语音与数据** |
|---|---|---|
| 关注 | 界面能看懂、演示不翻车、截图能进报告 | 推理快、语音稳、指标可量化 |
| 代码范围 | `ui/`、`main.py` | `feature.py`、`recognize.py`、`library.py`、`speech.py`、`asr_*.py`、`utils/`、`config.py`、`tools/`、`tests/` |
| 交付物 | 界面截图 ×6、演示脚本 | 评测 CSV、纯函数单测、性能日志 |
| 报告负责章节 | 交互设计、可视化、系统截图 | 特征提取、检索、准确率与未知判断分析 |

分工原则：**按文件所有权切，不按功能切**。一个功能如果跨文件，拆成"接口在 B、接线在 A"两段，各自在自己的文件里完成。

---

## 2. 文件所有权表（唯一权威）

| 路径 | 归属 | 说明 |
|---|---|---|
| `ui/app.py` | **A** | B 只读。若 B 需要一个回调，写进契约，由 A 接 |
| `ui/widgets.py` `ui/gallery.py` `ui/history.py` `ui/theme.py` | A（新建） | B 不碰 |
| `main.py` | A | 启动流程 |
| `infer_worker.py` | **B（新建）** | 推理线程封装，A 在 app.py 里 import |
| `feature.py` `recognize.py` `library.py` `speech.py` `asr_*.py` | B | A 只调用，不改 |
| `utils/camera.py` | B | 丢帧、FPS 统计在 B 侧完成 |
| `config.py` | **B** | 新增键由 B 加；A 侧一律 `getattr(config, "KEY", 默认值)` 兜底读，避免先后依赖 |
| `tools/` `tests/` | B（新建） | A 不碰 |
| `data/`（`library.json`、`library_feats.npy`、`data/register|test|unknown/`） | **A 统一提交** | B 的 PR 不得包含 `data/` 变更，防止二进制冲突 |
| `docs/PLAN.md` `docs/DIVISION.md` | 共同 | 改规则需在 PR 里说明并 @ 对方 |
| `docs/demo.md` `docs/img/` | A | |
| `docs/eval/`（CSV、混淆矩阵） | B | |
| `.github/` `README.md` `.gitignore` `.gitattributes` | 基座 | 需要改先提 issue |

> 一条硬规则：**PR 里出现别人名下的文件，必须由本人在 PR 描述里写"经 XX 同意"，否则不合并。**

---

## 3. 分支模型

```
main ─── ●──────────●───────────●──────────●─────►  只收 PR，不直接 push
          \        /  \        /  \       /
           feat/a1     feat/b3     feat/a4
```

| 规则 | 说明 |
|---|---|
| 分支命名 | `feat/a1-voice-loop`、`fix/b2-library-atomic`、`docs/…` |
| 粒度 | **一个 issue = 一个分支 = 一个 PR**，diff ≤ 400 行 |
| 生命周期 | 分支存活 ≤ 2 天；超过说明任务拆得不够细 |
| 同步方式 | 每天开工先 `git fetch && git rebase origin/main`（用 rebase，不要 merge main 到 feature） |
| 合并 | Squash merge，标题用 `feat(ui): 语音闭环 (#12)` |
| 谁合并 | **交叉评审**：A 的 PR 由 B approve，反之亦然。自己不能合自己的 |
| 冲突 | 若冲突出现在对方文件 → 说明越界了，先停下来在 issue 里对齐，不要硬解 |

---

## 4. 并行工作流（每天的操作顺序）

```
1. 拉最新：git switch main && git pull --ff-only
2. 建分支：git switch -c feat/a3-thumbnails
3. 做一件事，跑通，自测（见 PR 模板清单）
4. 提交：git commit -m "feat(ui): 录入页增加缩略图网格"
5. 推送：git push -u origin feat/a3-thumbnails
6. 开 PR：关联 issue，勾选自测清单，请求对方 review
7. 对方 approve 后 squash 合并，删除分支
```

**每日 1 次 5 分钟同步**（面对面或 issue 评论）只回答三句：
- 昨天合了什么
- 今天做什么（对应 issue 编号）
- 有没有碰到对方文件

---

## 5. 两个汇合点怎么处理

| 汇合 | 处理方式 |
|---|---|
| **B4 → A4** | B4 合并后，B 在 issue 里 @A 并附一段 10 行示例代码（怎么 new、怎么 connect、怎么 stop）。A 拉最新 main 后再开 A4 分支 |
| **B3 → A1（可选）** | 不阻塞。A1 先用现有 `recognize_speech_async` 完成闭环；B3 合入后 A 再花 30 min 把 `on_partial` 接到界面做"边说边出字幕" |

除此之外，**任何任务都不应等待对方**。如果需要等，说明拆分有问题，回到 PLAN.md 重新切。

---

## 6. 提交信息规范

```
<type>(<scope>): <中文简述>

type: feat | fix | perf | docs | test | chore | refactor
scope: ui | core | speech | library | camera | tools | docs
```

示例：
- `feat(ui): 识别成功自动播报并支持语音触发`
- `perf(core): 推理移入工作线程，主线程阻塞降到 16ms 内`
- `fix(library): 加载时校验特征行数，避免越界崩溃`

---

## 7. 各自的任务看板

### A 线

- [ ] **A1** 语音闭环（3 h）
- [ ] **A2** Top-3 条形图 + 未知徽标（4 h）
- [ ] **A3** 缩略图 + 库管理（4 h）
- [ ] **A4** 叠层 + 历史（5 h，等 B4）
- [ ] **A5** 启动闪屏 + 全屏 + 快捷键（4 h）
- [ ] **A6** 演示脚本 + 截图（3 h）

### B 线

- [ ] **B1** 启动器 + 配置（2 h）
- [ ] **B2** 特征库健壮性（4 h）
- [ ] **B3** 语音基础设施（6 h）
- [ ] **B4** 推理线程化（5 h）
- [ ] **B5** 识别质量（5 h）
- [ ] **B6** 批量工具与评测（6 h）

---

## 8. 冲突预案

| 现象 | 处理 |
|---|---|
| `data/library_feats.npy` 二进制冲突 | 以 A 的版本为准，B 侧 `git checkout --ours/--theirs` 后重新 `tools/batch_register.py` 生成 |
| 两人都在改 `config.py` | B 拥有该文件；A 提 issue 描述所需键，B 批量加 |
| CI 红了但不是自己的问题 | 在 issue 里贴 CI 日志，不要绕过；主干必须常绿 |
| 一方进度落后 | 先把已完成的 PR 合掉，把落后任务挪到下一个波次，不动别人的排期 |
