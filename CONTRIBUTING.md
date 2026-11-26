# 玩法挑战 · The throughput challenge

**大家一起来找：哪种玩法能持续处理更多有效 token，同时找到更好的数学解？** 欢迎有仓库访问权限的协作者提交已跑过的对照、未奏效的负结果，也欢迎提出下一位玩家可以复现的想法。速度目标是十分钟平均每分钟一亿有效 token。玩法不限定并发数。另设累计总量赛，供比较更长的连续工作量；两种成绩分开提交。

### 新研究题与吞吐成绩的区别

Cap Set、Golomb 尺和超维递归模拟器各有独立的数学裁判；这些游戏的得分不是速度赛或累计总量赛的成绩。已有吞吐数据来自原先的方格/四境驱动，不能当作新题的实测结果。正式单路场景 ID 与题包 ID 相同，共享入口为 `/research?game=<id>`。

| 题包 | 玩法与可比结果 | 场景 ID |
|---|---|---|
| `cap-set` | 四维三元空间找无三点共线点集；每点为 4 个 0/1/2 坐标，目标是扩大点集。 | `cap-set` |
| `golomb-ruler` | 8 刻度的两两差值互异尺，目标是缩短首尾长度；镜像去重。 | `golomb-ruler` |
| `lorenz-divergence` | 调初始值，求两条 Lorenz 轨迹首次达到分离阈值的时刻；数值结果双方法复核。 | `lorenz-divergence` |

扩展题包为 `cap-set-5d`、`cap-set-6d`、`cap-set-7d`、`cap-set-8d`、`golomb-ruler-12`、`golomb-ruler-16`，仍用同一裁判规则和对应 ID。8 维 Cap Set 可载入历史 401 点构造参考；只作为已复核构造，不宣称首创或最优，并与玩家自研构造分开标注。

## 选一组对照

| 玩法 | 可以试的问题 | 游戏里如何看结果 |
|---|---|---|
| 方格装箱 | 固定块数，在同一摆法上试不同移动或旋转策略 | 几何裁判认可的有效布局数量、外框是否缩短 |
| 四境驿站 | 给同样的名单，尝试不同换站策略和分支数 | 去重合法名单数、覆盖的站点对是否增加 |
| 同题多路 | 两路用不同的搜索方向；再增加并发观察等待时间 | 吞吐、重复候选和错误数 |
| 方格＋接力 | 让两个不同题型同时推进，比较单题与混合任务 | 两种裁判分别给出成绩 |
| 首轮预生成 | 同一场景对比即时生成和提前备题 | 开场首个回包、前一分钟与完整十分钟的有效吞吐 |

方格装箱由本机几何裁判检查单位方块布局是否越界、重叠，并记录有效布局与外框。四境驿站由本机裁判重算名单合法性及覆盖站点对。吞吐和数学成绩是独立指标。

从实验室的「离线演练」熟悉裁判后，可以选「开始三组对照」测试单路方格、方格双路和方格＋接力。要研究自己的方法，也可从命令行运行一个新批次；每次指定新的输出目录。例如，在已授权套餐的环境中运行：

```sh
python -B -X utf8 自动运行/benchmark.py --live --scenario mixed --out runs/mixed-trial-01
```

命令中的 `python` 指运行游戏的虚拟环境解释器。可用场景见 `python 自动运行/benchmark.py --help`。正式对照保持模型、推理强度、起始存档和持续时间一致，记录改动的变量。异常结束的试验也值得分享，请写明实际结束时刻和未确认的请求数。

### 方格分支搜索

在网页的并发菜单选择「方格与接力 · 各 40 路、各 10 分钟」，即可比较两款游戏的 1,152 分支搜索。每个方格分支都包含一组变化后的坐标、预估外框和五组紧邻方块；模型选择分支并提出位移，原生裁判检查有效性。若想比较近似输入体量的大题面，可单独测试 CLI 场景 `square-frontier-1728-40`。这一模式将真正可选的布局增加至 1,728 个；实际 TPM 以本次模型回包为准。先备题可使用 `自动运行/prepared_frontiers.py --help` 中的 `--pack square-26`、`--count` 和对应的场景名。

## 如何算成绩

1. **速度赛**：一个完整 600 秒窗口内，每份具有唯一响应 ID 的已完成回包计一次。有效 Token = 非缓存输入 + 输出；十分钟有效 Token 总量除以 10，即有效 Token/分钟。输出中的推理 Token 已包含在输出里。保留十个一分钟计数。
2. **累计总量赛**：总运行时长可变；只汇总连续序列中的不重叠完整 600 秒窗口，按唯一响应 ID 去重，每份回包最多计一次。必须注明总运行时长、完整窗口数和每窗/总有效 Token。未满 600 秒的尾段单独列出，不计入完整窗总量。
3. **数学成绩**：方格分别报告通过几何裁判的有效布局数、外框或最佳成绩；四境驿站报告去重合法候选、起始覆盖数和最佳覆盖数。短测、瞬时峰值、离线演练和未确认请求单列，不能代替以上赛制。吞吐与数学成绩分开列示。
4. **可选平台读数**：共享账户周额度是另一个指标；如有授权且愿意，可提供同一窗口内的平台百分点变化。它不等同于 Token 计数。准备阶段产生的本地字节没有模型用量。

对一个完整速度赛窗口，在本地运行以下命令。输出含模型、路数、十个分钟桶、输入/缓存/输出和去重候选数。校验器读取本地回执、检查重复项、重算裁判得分；模型用量来源仍是运行者的记录。

```sh
python -B -X utf8 benchmark_proof.py runs/mixed-trial-01/mixed --goal-window --target-per-minute 100000000
```

累计总量赛需要把两个或更多连续、不重叠的完整窗口目录按时间顺序提交：

```sh
python -B benchmark_proof.py --series <first-window-dir> <second-window-dir>
```

每个目录应对应一个完整 600 秒窗口。汇总按唯一回包 ID 去重，并同时报告实际总时长；不完整尾段另列。数学结果仍单独复算和报告。

## 提交给其他玩家

通过[玩法挑战 / Throughput method 表单](https://github.com/g6759/token-killer/issues/new?template=throughput-method.yml)提交方法、负结果或实测汇总；该仓库为私有，仅邀请已有仓库访问权限的协作者提交。也可以发 PR 加入可复现的实现。最有用的记录往往能回答三个问题：你改了什么，别人如何重跑，吞吐和数学成绩各发生了什么变化。

只分享获准在该协作范围共享的方法、负结果、脱敏回执汇总和可复现的最小示例。不要附上用户私下档案、私人跑分、个人/账户/任务 ID、quota 读数、绝对路径、原始私密请求/回复或授权材料。保留原文材料和其许可边界；私人原件及完整存档留在本机。

**English.** There are two separate metrics. A **speed run** uses one complete 600-second window: uncached input plus output, divided by 10, gives effective tokens per minute. A **cumulative-total run** may last any duration, but sums only non-overlapping complete 600-second windows, deduplicates by unique reply ID, and states total elapsed time. Report throughput and math outcomes separately. Square packing reports geometry-validated layouts and the enclosing square; Four Realms Relay reports unique valid candidates and covered station pairs. The new Cap Set, Golomb ruler and Hyperdimensional Recursive Simulator each have independent game judges; their scores are not throughput contest results. Existing throughput data came from Square packing and Four Realms, not these new games. Each research pack has a single-lane formal scenario with the same ID, opened at `/research?game=<id>`; variants are `cap-set-5d`, `cap-set-6d`, `cap-set-7d`, `cap-set-8d`, `golomb-ruler-12` and `golomb-ruler-16`. The optional 401-point 8D Cap Set is a checked historical reference, not a claim of priority or optimality, and is labeled separately from player-created work. The private repository accepts issue submissions only from contributors with repository access. Share methods, negative results and redacted receipt summaries only within source permissions; do not attach private archives/runs, IDs, quota readings, absolute paths or raw private material. Use the linked form to submit an idea or result. Pull requests with a reproducible implementation are welcome.
