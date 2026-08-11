---
name: finance-research-evolution
description: 当用户希望复用金融研究纠错、偏好记忆、数据源核验、微信简报发送或把金融任务复盘沉淀为可执行流程时使用。
---

# Finance Research Evolution

## 目标

把金融研究中的纠错、偏好、数据源经验、风险规则和成功流程沉淀为可复用能力。优先服务股票研究助手，不做自动交易。

## 触发场景

- 用户说“记住、以后都、纠正一下、这个数据源不对、复盘、自进化、沉淀成 skill”。
- 用户要求把研究报告、每日简报、提醒推送到微信或企业微信群。
- 任务暴露出标的解析、实时性、fallback 数据、网页失败、港股代码或风险边界问题。

## 工作流

1. 判断内容类型：偏好、纠错、数据源经验、风险规则、策略复盘或工具流程。
2. 金融查询先核验标的代码和数据时间；不要把模型旧知识当事实。
3. 调用 `finance_memory_add` 保存短小可复用事实，避免保存一次性长日志。
4. 如果有完整成功轨迹或复盘，调用 `finance_evolve_from_trace` 写入 memory；核心 `finance-research-evolution` Skill 保持稳定，不要默认覆盖。
5. 如果用户明确要求生成新的专用 Skill，使用独立 skill 名称，不覆盖本 Skill。
6. 如果用户要求微信通知，先调用 `wechat_status`；未配置 webhook 时说明会写入本地 outbox，再调用 `wechat_send`。
7. 结束时说明写入位置、是否发出消息、需要用户配置的环境变量。

## 金融特化规则

- 数据源经验必须包含市场、代码、来源和时间约束。
- 标的纠错优先沉淀为“解析流程”，不要只硬编码一个结论。
- 对 `SAMPLE_FALLBACK`、WAF、403、No route to host、Yahoo 延迟等问题，要记录避免误判的方法。
- 风险规则要写成研究边界，例如“不输出确定性买卖指令”“回测不承诺收益”。
- 微信 webhook、token、cookie、API key 不得进入 Skill 或仓库。

## 输出格式

沉淀完成后输出：

- Memory: 写入位置或“未写入”的原因
- Skill: 更新路径或“不需要更新”的原因
- WeChat: sent / queued / disabled
- Next: 用户还需要配置或验证的事项
