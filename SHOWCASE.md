---
title: Problem-Reductions Benchmark — Showcase & User Guide
date: 2026-06-25
tags:
  - benchmark
  - guide
  - showcase
---

# Problem-Reductions Benchmark — Showcase & User Guide

> Repo: https://github.com/Ferrari-72/problem-reductions-benchmark
> Leaderboard & submission: Hugging Face Space (Gradio) — see `SUBMISSION.md`
> Library: https://github.com/CodingThrust/problem-reductions (pinned commit `aa2d1a1`)

---

## 一、这个 Benchmark 是什么

**核心问题**：对于同等计算预算，哪个 LLM 能在 problem-reductions 库中找到最多 bug？

**工作流**：

```
LLM Agent
  │
  ├─ pred create       # 构造小的 source 实例
  ├─ pred reduce       # A → B（reduction bundle）
  ├─ pred solve        # 求解 B，得到 target config
  ├─ pred extract      # target config → source config
  └─ pred evaluate     # 验证提取出的 source config 是否合法
         │
         └─ 不合法 → 生成 CERTIFICATE（bug 凭证）
                │
                └─ Verifier 独立复核 → accepted / rejected
                         │
                         └─ accepted → 写入 results/*.json → Leaderboard
```

**核心指标**：`bugs_found` —— 在固定 commit 上，**有 ≥1 个确认 bug 的不同 rule 数**（一条 rule 只记一个，无论提交多少反例）。该指标完全可验证、不可灌水。`bugs / K tokens` 和 `bugs / $` 为效率参考指标（分母是自报 token/成本），仅用于并列时打破平手。

---

## 二、判定 bug 的唯一标准：round-trip

一条 rule A→B 在实例 `a` 上正确,当且仅当**直接求解**与**经 reduction 求解**结果一致(优化问题比**值**,判定问题比**可行性**):

```
solve(a)  ==  solve(reduce(a))
```

`pred solve <bundle>` 本身就做完了整个 round-trip(解 target → extract 回 source → 在 source 空间求值),所以只需把它和 `pred solve <source>` 比较。不一致即真 bug,verifier 自己重跑 `pred`,**从不信 AI 的声明**。不一致会被打上派生标签:

| 标签 | 含义 |
|------|------|
| `optimum_not_preserved` | 两边都可行,但 round-trip 的值不同 |
| `feasibility_not_preserved` | source 有解,但 round-trip 无解 |
| `spurious_solution` | round-trip 声称有解,但 source 实际无解 |

可选地,certificate 带一个 `target_config`(某个具体的 target 解)还能额外抓到**抽取层** bug——`unsound_extraction`(合法 target 解 extract 回非法 source 解)和 `suboptimal_extraction`(最优 target 解 extract 回次优 source 解),这些是求解器自己的最优解掩盖不掉的。只比**值**、不比**具体解**,所以多最优解不会误判。

---

## 三、如何添加新模型并提交结果

### 3.1 环境准备

```bash
# 1. Clone benchmark repo
git clone https://github.com/Ferrari-72/problem-reductions-benchmark
cd problem-reductions-benchmark

# 2. Clone 并 pin 库到指定 commit
git clone https://github.com/CodingThrust/problem-reductions
cd problem-reductions && git checkout aa2d1a1 && cd ..

# 3. 安装依赖
pip install -e ".[dev]"

# 4. 确认 pred CLI 可用
pred --version
```

### 3.2 配置 API Key

根据模型设置环境变量：

```bash
# OpenAI
export OPENAI_API_KEY=sk-...

# Anthropic Claude
export ANTHROPIC_API_KEY=sk-ant-...

# DeepSeek
export DEEPSEEK_API_KEY=sk-...

# Windows PowerShell
$env:OPENAI_API_KEY = "sk-..."
$env:PYTHONUTF8 = "1"   # Windows 必须，防止 emoji 导致 GBK 编码错误
```

### 3.3 运行 Benchmark

```bash
python -m benchmark.run_mini \
  --model deepseek/deepseek-chat \
  --api-base https://api.deepseek.com/v1 \
  --repo-dir path/to/problem-reductions \
  --budget 5.0 \
  --per-rule 0.5 \
  --output results/deepseek_deepseek-chat.json \
  --trajectory-dir results/trajectories
```

**参数说明**：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--model` | LiteLLM 格式的模型名 | `anthropic/claude-sonnet-4-6` |
| `--api-base` | 自定义 API 地址（第三方模型需要） | None |
| `--budget` | 总预算上限（USD） | 20.0 |
| `--per-rule` | 每条 rule 的预算上限 | 0.5 |
| `--rules` | 只测指定 rule（空=全部） | all |
| `--trajectory-dir` | 保存 agent 轨迹 JSONL 的目录 | None |

### 3.4 验证结果格式

```bash
python -c "
import json, sys
with open('results/YOUR_MODEL.json') as f:
    d = json.load(f)
required = ['model','library_commit','bugs_found','total_cost_usd',
            'total_tokens_k','efficiency_bugs_per_ktok',
            'efficiency_bugs_per_dollar','rules_tested','results']
missing = [k for k in required if k not in d]
print('MISSING:', missing if missing else 'none — schema OK')
print(f'bugs={d[\"bugs_found\"]}, cost=\${d[\"total_cost_usd\"]:.4f}, tokens={d[\"total_tokens_k\"]}K')
"
```

### 3.5 打包并提交结果

不再走 "results/ + GitHub Pages + PR" 那套。现在用 Docker runner 产出一个自描述的 `submission.json`,提交到 HF Space,后端零信任重验每条 certificate 并自动排名:

```bash
make submission          # → ./out/submission.json（真实跑分，需 API key + 价格）
# 然后在 Space 的 Submit 页上传 out/submission.json
```

详见 `SUBMISSION.md`(价格 / 预算硬上限 / 提交字段)。

---

## 四、值得展示的亮点

### 4.1 完整 Pipeline 运行（Pipeline Demo）

运行一条 rule 的完整流程，展示 agent → certificate → verify → leaderboard 全链路：

```bash
# 跑单条 rule，用 --rules 指定，只需 ~$0.05
python -m benchmark.run_mini \
  --model deepseek/deepseek-chat \
  --api-base https://api.deepseek.com/v1 \
  --repo-dir path/to/problem-reductions \
  --rules exactcoverby3sets_subsetproduct \
  --per-rule 0.5 \
  --output results/demo.json \
  --trajectory-dir results/demo_traj
```

### 4.2 Agent 轨迹可视化（Trajectory Inspection）

每条 rule 的 `--trajectory-dir` 会保存一个 JSONL 文件，记录 agent 的完整思考过程：

```bash
# 查看 agent 对某条 rule 的推理过程
cat results/trajectories/deepseek_deepseek-chat_exactcoverby3sets_subsetproduct.jsonl \
  | python -c "
import sys, json
for line in sys.stdin:
    m = json.loads(line)
    if m['role'] == 'assistant':
        print('=== AGENT ===')
        print(m['content'][:500])
        print()
"
```

可以看到 agent 实际执行的 `pred` 命令、对 reduction 逻辑的推理分析——验证模型真的在"思考"而不是猜测。

### 4.3 Leaderboard

榜单在 HF Gradio Space 上(`space-gradio/`,带 Submit 页):
- `bugs_found`(去重后的 rule 数)是横向对比不同模型的核心指标
- 按 `bugs_found` 排序,并列时用 `efficiency_bugs_per_ktok` 打破平手;后端重验通过后自动上榜
- 自报美元仅作参考(价格由提交者声明),效率主指标是 bugs/Ktok

### 4.4 独立 Verifier（Zero Trust）

核心设计亮点：verifier **不信任** AI 提供的任何值,完全自己重跑——它从 `source` 用 `pred reduce` 重新推导 bundle,再用 `pred solve` 做 round-trip:

```
# 直接解 source           → pred solve source        → 比较
# 经 reduction round-trip → pred solve reduce(source) → 值/可行性不一致即 bug
# 若带 target_config，再独立 extract + evaluate，抓抽取层 bug
```

AI 无法"捏造" bug 凭证——certificate 里写什么值都会被忽略,verifier 全部重算后判定,错误或非最小的凭证直接 `rejected`。

### 4.5 测试套件（全 mock，无需 API key）

```bash
pytest benchmark/tests/ -q 2>&1 | tail -3
# 191 passed, 4 skipped
```

单元测试 monkeypatch 了 `PredSolver`,不依赖真实 API key；集成测试(`-m integration`)才需要 `pred`。任何人 clone 后可立即运行单元测试。

---

## 五、已知问题与注意事项

### 5.1 当前版本 (v0.6.0 / `aa2d1a1`) 的 bug 分布

`aa2d1a1` 的大多数 reduction rules 是正确的,但**确实存在真实的 reduction bug**——我们在这个固定 commit 上已用 round-trip verifier + 原生 `pred` 复核确认了多个反例(集中在较冷门、加权 / 边界输入的规则上)。具体反例是评测的"答案",不在公开仓库里(见 `benchmark/tests/fixtures/private/`,已 gitignore)。

因此:
- **0 bugs 不代表库没 bug**,而是 agent 没找到——这正是模型间拉开差距的地方
- 越冷门、越少被走过的规则(以及加权 / 退化 / 空或零值输入)越值得查
- 评分基于在 `aa2d1a1` 上 `pred` 可复核的反例,与是否"新"无关

### 5.2 Windows 特有问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `UnicodeEncodeError 'gbk'` | minisweagent 输出 emoji，Windows 控制台默认 GBK | 运行前设置 `$env:PYTHONUTF8="1"` |
| `pred` 不能读 stdin | Windows 管道限制 | agent 提示词已说明：先写文件再传路径 |
| Git push 失败 | 需要代理 | `git config --global http.proxy http://127.0.0.1:7890` |

### 5.3 step_limit 调优

默认 `step_limit=35`，建议范围：

| 场景 | 推荐值 |
|------|--------|
| 快速验证 pipeline 是否工作 | 20（够跑完一次 round-trip） |
| 正式评测，有充足预算 | 35–50 |
| 复杂 rule（如图论问题） | 50+ |

step_limit 太低时，agent 会花完所有步数读代码，没有时间跑 `pred reduce/solve/extract`，直接返回 `no_certificate`。

### 5.4 Cost 估算

| 模型 | 每条 rule 平均 cost | 15 rules 总计 |
|------|-------------------|--------------|
| DeepSeek Chat | ~$0.04 | ~$0.63 |
| Claude Sonnet 4.x | ~$0.15 | ~$2.25 |
| GPT-4o | ~$0.10 | ~$1.50 |

`--per-rule 0.5` 是安全上限，大多数模型每条 rule 花不到这个数。

### 5.5 求解超时的局限

round-trip 判定需要 `pred solve` 两边(优先 ILP,退 brute-force)。对于大实例,求解可能超时;此时 verifier 返回 `rejected`(不崩溃,超时不算证明),该实例上的 bug 无法被确认。

**建议**:用**最小**反例(几个节点 / 几条子句),既快又是更强的见证;verifier 也会拒绝过大的 source(> 256KB)。

---

## 六、目录结构速览

```
problem-reductions-benchmark/
├── benchmark/
│   ├── run_submission.py     # 主入口：跑预算内 session → submission.json
│   ├── run_mini.py           # 单条 rule 的 agent 会话
│   ├── scheduler.py          # 多模型/多规则调度 + 预算上限
│   ├── cost.py               # token×价格 自算成本（硬上限）
│   ├── verify.py             # 独立 verifier（zero trust，round-trip）
│   ├── verify_submission.py  # 后端打分（重验每条 cert）
│   ├── backend_score.py      # 提交队列评分 + webhook 入口
│   ├── config.yaml           # agent prompt + step_limit
│   └── tests/                # 单元测试
├── space-gradio/             # HF Gradio Space（榜单 + Submit）
├── docker/Dockerfile         # runner 镜像（pred + agent）
└── SHOWCASE.md               # 本文件
```

---

## 七、快速开始（3 条命令）

```bash
# 1. 准备环境
git clone https://github.com/Ferrari-72/problem-reductions-benchmark && cd problem-reductions-benchmark
pip install -e ".[dev]"

# 2. 设置 API key（以 DeepSeek 为例）
export DEEPSEEK_API_KEY=your_key_here  # 或 $env:DEEPSEEK_API_KEY="..." on Windows
export PYTHONUTF8=1  # Windows 必须

# 3. 跑 1 条 rule 验证 pipeline
python -m benchmark.run_mini \
  --model deepseek/deepseek-chat \
  --api-base https://api.deepseek.com/v1 \
  --repo-dir path/to/problem-reductions \
  --rules knapsack_subsetsum \
  --output results/my_test.json
```

---

*Generated: 2026-06-25 | Repo: https://github.com/Ferrari-72/problem-reductions-benchmark*
