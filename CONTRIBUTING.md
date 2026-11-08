# 协作指南

两人协作，规则很薄，但必须遵守。

## 1. 三份必读

1. [docs/PLAN.md](docs/PLAN.md) —— 做什么、做到什么程度算完成
2. [docs/DIVISION.md](docs/DIVISION.md) —— 谁改哪些文件、怎么开分支
3. [docs/CONTRACT.md](docs/CONTRACT.md) —— 跨人调用的函数签名

## 2. 开一个任务

```bash
git switch main && git pull --ff-only
git switch -c feat/a3-thumbnails          # feat/ | fix/ | perf/ | docs/ + 任务号
```

任务号对应 GitHub issue 标题里的 `[A3]`。**一个分支只做一件事**，diff 超过 400 行就拆。

## 3. 提交

```
feat(ui): 识别成功自动播报并支持语音触发
fix(library): 加载时校验特征行数，避免越界崩溃
perf(core): 推理移入工作线程，主线程阻塞降到 16ms 内
```

`type(scope): 中文简述`，scope 取 `ui | core | speech | library | camera | tools | docs`。

## 4. 自测清单（PR 里逐条勾）

- [ ] 程序能启动（`python main.py`），三个 Tab 都能点开
- [ ] 本次改动涉及的路径**手动走一遍**（不是只看代码）
- [ ] 没有新增 `print` 调试输出、没有提交 `data/` 变更（除非是 A 的库数据任务）
- [ ] 没有改动别人名下的文件（对照 DIVISION 所有权表）
- [ ] 若改了公共签名，同步更新 `docs/CONTRACT.md`
- [ ] `ruff check object_recognition` 通过

## 5. 提 PR

PR 模板会自动带出检查项。合并需满足：

1. CI 绿（ruff + 编译检查）
2. 对方 approve（自己不能合自己的 PR）
3. 关联了 issue（`Closes #12`）

用 **Squash merge**，合并后删除分支。

## 6. CI 做什么、不做什么

`.github/workflows/ci.yml` 只做两件事：`ruff check` 和 `python -m compileall`。

> ⚠️ **首次启用 CI**：推送 `.github/workflows/*` 需要 token 具备 `workflow` scope，普通 `repo` scope 会被拒。模板内容见 [`docs/ci-workflow.yml`](docs/ci-workflow.yml)，启用方式二选一：
> - 网页端：Add file → 路径 `.github/workflows/ci.yml` → 粘贴模板内容 → 提交
> - 命令行：`gh auth refresh -h github.com -s workflow` 后 `git add .github/workflows/ci.yml && git push`

**不装 torch**（Runner 上 2 GB 依赖不划算），所以：

- 逻辑必须能在**纯 Python** 下测：检索打分、意图解析、评测指标 → 放 `tests/`
- 依赖模型/麦克风的路径，用真实设备本地验证，PR 描述里写"已在本机验证：xxx"

本地跑一遍等价检查：

```bash
ruff check object_recognition tests tools
python -m compileall -q object_recognition
python -m pytest tests -q          # 装了 pytest 才有
```

## 7. 遇到问题

- 发现自己要改对方的文件 → 停下，在 issue 里说，由对方改或授权
- CI 红了不是自己引起的 → issue 里贴日志，别绕过，主干必须常绿
- 契约需要变更 → 单独 PR 改 `CONTRACT.md` 并 @ 对方，合并后再改代码
