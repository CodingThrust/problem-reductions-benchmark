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
> Leaderboard: https://ferrari-72.github.io/problem-reductions-benchmark/
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

**核心指标**：`bugs / K tokens`（每千 token 找到的 bug 数）—— 比 `bugs / $` 更能跨模型比较，因为不同模型定价差异大。

---

## 二、5 种 Violation 类型

| 类型 | 含义 | 需要 Solver | AI 需提供 |
|------|------|:-----------:|-----------|
| `unsound_extraction` | extract 出的解不合法 | 否 | `target_config` |
| `incomplete_reduction` | source 有解但 target 无解 | 是 | 无 |
| `suboptimal_extraction` | 提取出的解比最优差 | 否 | `target_config` + `brute_force_solution` |
| `solve_mismatch` | source/target 的 evaluation 不一致 | 是 | 无 |
| `order_reversal` | 目标空间排序与源空间排序相反 | 否 | `target_config_lo` + `target_config_hi` |

**Solver-free 优先**：`unsound_extraction`、`suboptimal_extraction`、`order_reversal` 不依赖 `pred solve`，可以处理大实例；另两种受超时保护，超时返回 `rejected`，不崩溃。

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

### 3.5 重建 Leaderboard

```bash
python benchmark/build_index.py
# 生成 results/index.json，GitHub Pages 自动读取并渲染 leaderboard
```

### 3.6 提交结果（PR）

```bash
git checkout -b results/YOUR_MODEL_NAME
git add results/YOUR_MODEL.json results/index.json
git commit -m "add YOUR_MODEL results: N rules, XK tok, Y bugs"
git push origin results/YOUR_MODEL_NAME
# 然后在 GitHub 开 PR
```

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

### 4.3 Leaderboard 实时数据

访问 https://ferrari-72.github.io/problem-reductions-benchmark/ 展示：
- 当前已有 DeepSeek (`deepseek/deepseek-chat`) 的真实跑分
- `efficiency_bugs_per_ktok` 是横向对比不同模型的核心指标
- 表格按该指标排序，新提交的模型会自动出现

### 4.4 独立 Verifier（Zero Trust）

核心设计亮点：verifier **不信任** AI 提供的任何值，完全自己重跑：

```
# unsound_extraction: verifier 自己重新 extract，再 evaluate
# incomplete_reduction: verifier 自己 solve source 和 bundle
# solve_mismatch: verifier 自己 solve 两边，比较结果
```

AI 无法"捏造" bug 凭证——即使提供假的 `claimed_source_solution`，verifier 也会独立验证并拒绝。

### 4.5 测试套件（185 tests，全 mock，无需 API key）

```bash
cd benchmark && pytest tests/ -v 2>&1 | tail -5
# 185 passed, 3 skipped
```

所有测试 mock 了 `_run_pred`，不依赖真实 API key，任何人 clone 后可立即运行。

---

## 五、已知问题与注意事项

### 5.1 当前版本 (v0.6.0 / `aa2d1a1`) 的 bug 分布

**重要**：`aa2d1a1` 提交的 reduction rules 大部分是正确的。GitHub 上标注 "Wrong" 的 issue 描述的是**尚未实现的 rule**（future work），而不是现有 rule 的 bug。

因此：
- **在 `aa2d1a1` 上跑出 0 bugs 是正常结果**，说明 agent 分析是准确的
- 若要测试 bug 发现能力，需要切换到包含已知 bug 的库提交版本
- 后续可以在库的 `main` 分支（更新的 commit）上重新测试

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

### 5.5 Solver-based violations 的局限

`incomplete_reduction` 和 `solve_mismatch` 需要运行 `pred solve`。对于大实例（节点数 > 15），默认 solver 可能超时（120s）。超时时 verifier 返回 `rejected`（不崩溃），但这类 violation 无法被检测到。

**建议**：优先设计使用 solver-free violation 类型（`unsound_extraction`、`order_reversal`）的 agent 策略。

---

## 六、目录结构速览

```
problem-reductions-benchmark/
├── benchmark/
│   ├── run_mini.py          # 主入口
│   ├── verify.py            # 独立 verifier（zero trust）
│   ├── config.yaml          # agent prompt + step_limit
│   ├── env_setup.py         # pred 环境检查
│   └── tests/               # 185 个单元测试
├── results/
│   ├── deepseek_deepseek-chat.json   # 已有跑分
│   ├── index.json           # leaderboard 数据源
│   └── trajectories/        # agent 轨迹 JSONL
├── docs/
│   └── index.html           # GitHub Pages leaderboard
└── SHOWCASE.md              # 本文件
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
