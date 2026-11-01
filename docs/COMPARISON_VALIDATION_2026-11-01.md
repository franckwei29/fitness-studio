# 对话模型比较：验证与交付记录（2026-11-01）

## 交付行为

在对话中输入“比较 Canary 当前的 Coach 和 Gemini，保持相同指令和测试用例，等我确认”，系统自动准备准确提案，一次 **Confirm and run** 启动比较。缺少信息时在聊天内追问，进度和输出也留在聊天中。

版本 ID、分组 ID、角色、模型可用性、同模型请求和配置快照均有后端校验。旧错误提案显示 Update needed，不能继续确认。比较只产生评估记录，不改变分组使用的模型。

## 自动化证据

- 完整后端回归：421 passed；随后针对新增及修正行为运行 owner 专项，最终 24 passed。
- 提供方记账、比较生命周期及 owner 补充回归：53 passed；真实用量未知保护专项：1 passed。上述集合有重叠，不累加为独立测试总数。
- 完整浏览器夹具回归：94 passed。前端构建、修改文件 Ruff 检查和 diff 检查通过。
- 覆盖空版本库、复用候选、显式分组版本、错误 ID/类型、跨角色、快照变更、重复确认、回滚、取消、候选歧义、缺少凭据、提案自动修正、对话上下文、已知用量格式错误的有限重试与未知用量暂停。
- 自动化故障注入使用独立测试数据库及模拟响应；没有故意耗尽实际业务数据库预算或伪造真实提供方成功。

## 真实浏览器验证

先在原数据库验证，再创建独立空数据库复测。发现新问题后，在两种数据状态下复查受影响场景。

| 场景 | 已有数据库 | 从空库开始的验证环境 |
|---|---|---|
| 中文直接请求、英文截图原句、口语/模型别名 | 通过 | 通过 |
| 相同指令和测试用例、等待确认 | 通过 | 通过 |
| 单次确认后自动创建/复用版本并启动 | 通过 | 通过 |
| 首次使用前没有 Coach 版本，预览不创建版本 | SQL 覆盖 | 真实验证通过 |
| 取消后重新准备、多轮补充信息 | 通过 | 通过 |
| 缺少分组/角色、错误分组、未知模型、同模型 | 通过 | 通过 |
| 修改角色，旧提案失效 | 通过 | 通过 |
| 修改目标为当前模型，再改回 Gemini | 修正后通过 | 修正后通过 |
| 刷新后聊天内查看结果、重复确认不重复启动 | 通过 | 通过 |

真实比较记录如下。Completed / FailedChecks 是模型输出的检查结果：两种状态都表示本轮所有测试输出已经记录，不等于系统故障。未通过的输出没有被改成成功。

| 比较 ID | 结果状态 | 两个模型的规定检查 | 实际评估调用 |
|---|---|---|---|
| `6ed6ee60-3346-41da-8d26-bc54cfe20478` | Completed | gpt-6-luna: 3/3 / gemini-3.5-flash-lite: 3/3 | 6 |
| `5f75f1b1-c3ba-462b-b29b-d4908c1dbb17` | FailedChecks | gpt-6-luna: 2/3 / gemini-3.5-flash-lite: 3/3 | 6 |
| `c4187190-063c-4310-b5a6-c6c55490a898` | FailedChecks | gpt-6-luna: 2/3 / gemini-3.5-flash-lite: 3/3 | 6 |
| `feabcbf9-ee3b-4cce-bb67-0a23c18526c1` | Completed | gpt-6-luna: 3/3 / gemini-3.5-flash-lite: 3/3 | 6 |
| `ae113553-a074-4e5f-9e81-a5d127beb91a` | FailedChecks | gpt-6-luna: 3/3 / gemini-3.5-flash-lite: 2/3 | 6 |

共 30 次评估调用；上述比较的未知用量总数为 0。此计数不含管理聊天的模型调用。真实质量检查不代表全面的教练质量评价。

## 实测发现并修复

1. 原错误将 Canary 分组 ID 用作候选版本 ID。现在生成前校验，历史卡片也会拦截；截图对应原提案已实际返回 NeedsCorrection。
2. 首次空库时模型误认为“相同指令和用例”是额外选项。工具说明现在明确这是内建保证；修正后空库首条请求一次确认启动。
3. 修改为无效目标后再改回时多问确认。现在保留比较上下文，并由后端根据语义修改标记定位并撤回旧提案。
4. 一次真实管理模型响应 JSON 不合法，但带有完整用量。旧行为把它暂停为未知用量；已按保存的提供方回执精确结算 392 micro-USD，保留审计并关闭该测试请求。新逻辑对有可靠用量的格式错误在原四轮预算内自动重试；没有用量证据时仍暂停。

## 最终空白环境

日常入口：http://127.0.0.1:8088

Compose 项目：`fitness-studio-clean-20261101`。本地私有配置：`.env.comparison-final`（已忽略，不提交）。旧数据库及验证数据库均保留，仅停止对应服务。

交付前检查：

```json
{
  "counts": {
    "User": 1,
    "Member": 0,
    "ChatSession": 0,
    "Request": 0,
    "Message": 0,
    "AgentVersion": 0,
    "EvaluationRun": 0,
    "ModelCall": 0
  },
  "groups": [
    "Canary",
    "Default"
  ],
  "model_calls_enabled": true,
  "default_model": "gpt-6-luna",
  "openai_configured": true,
  "gemini_configured": true,
  "global_budget_limit_usd": 20.0,
  "global_budget_spent_micros": 0,
  "global_budget_reserved_micros": 0,
  "source_matches_workspace": true,
  "ready_status": 200
}
```

正常初始化仅包含初始老板账户、Canary/Default 分组和默认基础配置。最终环境没有写入测试 Prompt、候选版本或比较记录。浏览器已登录并停在空白聊天输入页，供用户自行尝试。

在项目目录重新启动最终环境：

```sh
docker compose --env-file .env.comparison-final -p fitness-studio-clean-20261101 -f compose.yaml -f compose.app.yaml up -d
```

详细的本地对话记录及截图保存在 `.local/comparison-validation/`；测试对话已标注 Comparison QA。原环境卷 `fitness-studio-main-test03_mysql_data` 保留，验证卷也未删除。
