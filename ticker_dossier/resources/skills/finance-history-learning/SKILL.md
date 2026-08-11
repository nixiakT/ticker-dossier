---
name: finance-history-learning
description: 当用户要求根据历史行情学习预测规则、用历史数据校准股票方向/仓位、或把预测经验沉淀为 Skill 时使用。
---

# Finance History Learning

## 适用场景

用于从历史 K 线中学习可解释的方向预测规则，并把结果用于后续股票研究、纸面组合和预测评分。

## 最新学习结果

- 标的: `AAPL`
- 学习时间: `2026-07-08T05:08:58Z`
- 预测周期: `20` 天
- 历史样本: `421`
- 当前方向: `up`
- 启发式信号强度: `0.59`（非统计概率）
- 期望收益: `1.43%`

## 当前特征

- `trend_20d`: `flat`
- `trend_60d`: `strong_up`
- `price_vs_ma20`: `above`
- `price_vs_ma60`: `above`
- `volatility`: `normal`
- `rsi`: `strong`

## 历史匹配表现

- `trend_20d=flat`: n=75, win=50.7%, avg_forward=0.65%
- `trend_60d=strong_up`: n=86, win=69.8%, avg_forward=1.86%
- `price_vs_ma20=above`: n=251, win=54.6%, avg_forward=1.11%
- `price_vs_ma60=above`: n=248, win=56.0%, avg_forward=1.31%
- `volatility=normal`: n=421, win=58.9%, avg_forward=1.43%
- `rsi=strong`: n=150, win=63.3%, avg_forward=2.29%

## 使用规则

1. 先核验行情来源和时间，不能用 `SAMPLE_FALLBACK` 当真实学习数据。
2. 预测必须输出方向、周期、信号强度、历史样本数和主要匹配特征；未经校准的信号不得表述为概率。
3. 只把学习结果作为研究假设；不得承诺收益或输出确定性买卖指令。
4. 给出预测后调用 `prediction_record` 或 `/predict record` 保存 baseline。
5. 到期后调用 `prediction_evaluate` 和 `prediction_learn` 做事后评分。
6. 若样本数低于 60 或匹配特征不足，信号强度不得高于 0.5。

## 注意

- 每次预测后必须写入 prediction ledger，并在到期后评分。
