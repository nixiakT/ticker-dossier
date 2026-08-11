---
name: csv-quick-report
description: 当用户给一个 CSV 文件并想要快速统计概览/出一份 markdown 报告时使用。
---

# CSV 快速报告 Skill（示例）

这是一个**示例 Skill**，演示 SKILL.md 的结构。Day9 请把它替换/补充为**你所在领域**的 Skill。

## 何时使用
用户提供了 `.csv` 文件，并希望得到：行列规模、每列类型、缺失值、关键数值列的均值/分位数，最后汇成一份 markdown 报告。

## 步骤
1. 用 `read` 或 `bash`(head) 先看 CSV 前几行，确认分隔符与表头。
2. 用 `bash` 跑一小段 python（pandas）统计：`df.describe()`、`df.isna().sum()`、`df.dtypes`。
3. 把结果整理成 markdown 表格。
4. 用 `write` 写出 `report.md`，并向用户给出结论摘要。

## 注意
- 文件可能很大：先看规模再决定是否全量读入。
- 不要臆造列名，一切以 `read` 到的表头为准。

## 可用资源（可选）
- `scripts/summarize.py`：如果你为该 skill 附带脚本，放在这里并在上面引用。
