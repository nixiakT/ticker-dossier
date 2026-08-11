---
name: trace2skill
description: 当用户希望系统从一次成功任务、操作轨迹、工具调用日志、复盘记录或经验总结中提炼可复用 Skill，实现技能自进化、沉淀流程、更新项目 skills 目录时使用。
---

# Trace2Skill

## 目标

把一次成功任务的轨迹沉淀成可复用 Skill。Skill 必须短小、可触发、可执行，默认写入项目的 `skills/<skill-name>/SKILL.md`。

## 工作流

1. 读取用户提供的任务目标、成功轨迹、工具调用、踩坑点和最终结果。
2. 提炼可复用模式，不记录一次性细节、密钥、个人隐私或无关日志。
3. 为 Skill 生成小写短横线名称。
4. 写入 frontmatter：
   - `name`
   - `description`
5. 正文保留：
   - 适用场景
   - 输入要求
   - 操作步骤
   - 验证方法
   - 注意事项
6. 运行 Skill loader 或自检，确认新 Skill 可被发现。

## 质量标准

- description 必须包含触发场景。
- 正文只写另一位 Agent 真正需要的程序性知识。
- 不写 README、安装指南、冗长背景。
- 不包含 API key、token、cookie、账号、真实个人信息。
- 如果轨迹中出现失败步骤，只保留“如何避免/修复”的经验。

## 推荐工具

调用 `trace2skill_generate` 生成或更新项目内 Skill。
