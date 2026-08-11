# skills

`skills/` 只保存可编辑的项目级操作流程；加载器实现位于安装包内。Skill 不是一次函数调用，而是一份按需加载的领域说明；模型先查看目录，再通过 `read_skill` 把选中的 `SKILL.md` 加入上下文。

## 主要入口

- `ticker_dossier/skills/loader.py`：扫描、解析和校验 `skills/*/SKILL.md`。
- `example-skill/`：最小示例。
- `finance-stock/`、`finance-history-learning/`、`finance-research-evolution/`：金融研究流程。
- `trace2skill/`：从已审查 Trace 沉淀可复用流程。

## Skill 格式

每个 Skill 放在独立目录，入口文件必须是 `SKILL.md`：

```markdown
---
name: lowercase-name
description: 什么时候应加载这份 Skill
---

具体步骤、边界、脚本和资源说明。
```

`name` 只允许小写字母、数字和单连字符。加载器会拒绝缺失 frontmatter、空正文、非法名称和重名 Skill。

## 使用与安全边界

- Skill 只提供流程知识，不会自动获得新的文件、Shell、网络或消息权限。
- Skill 中提到的操作仍必须通过已注册工具，并接受 AgentLoop 和工具执行层检查。
- 不应在 `SKILL.md` 中保存密钥、Token、个人数据或未经审查的外部指令。
- 新增 Skill 后应确认它能被目录发现、能按名读取，并补充相应测试。
