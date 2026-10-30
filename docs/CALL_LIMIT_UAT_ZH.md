# AI 额度管理：人工验收补充

入口：使用现有老板账号登录后，打开侧边栏 **AI usage & limits**。仍保留统一的 **Assistant** 主聊天框和原有业务子页面。

所有改动都请记录“实际结果、Pass / Fail、截图”。不要通过真实发送几百次请求来耗尽付费额度。

| 编号 | 操作 | 预期结果 | 实际结果 / 状态 |
| --- | --- | --- | --- |
| Q01 | 在 Studio limits 中找到 GPT-6 Luna，选择 500，点 Save changes | 显示 100→500 成功回执；已用次数不清零；Gemini 不变 | 待人工填写 |
| Q02 | 将 Luna 恢复为 100 | 保存后分母立即变为 100，次数不清零 | 待人工填写 |
| Q03 | 同时打开两份设置页，各自编辑同一额度；先保存第一份，再保存第二份 | 第二份提示规则已变更，保留草稿，不能覆盖新设置；可主动刷新后重试 | 待人工填写 |
| Q04 | 在 Member allowances 点击实际某组的 Edit group allowance | 显示每位会员的默认次数、组内人数，以及受此次修改影响的继承人数 | 待人工填写 |
| Q05 | 搜索会员姓名或邮箱，点 Edit allowance；选择 Set custom allowance，填 300 并保存 | 标记 Custom allowance，生效额度 300；其他会员不变 | 待人工填写 |
| Q06 | 在该会员上选择 Use group default，点 Restore group default | 标记 Group default，跟随当前组的默认额度；当天用量不清零 | 待人工填写 |
| Q07 | 勾选 Show members with custom allowances | 只显示存在个人例外的会员，可点 View member 进入具体会员 | 待人工填写 |
| Q08 | 在已有个人例外的测试会员详情中换组 | 先提示个人例外会保留；换组后个人额度和用量不变 | 待人工填写 |
| Q09 | 在继承额度的测试会员详情中换组 | 生效额度跟随新组；用量不重置；保留原有换组校验 | 待人工填写 |
| Q10 | 展开 Owner allowance 和 Initial account allowance | 老板自己与入会前账号的额度分别展示，可独立保存 | 待人工填写 |
| Q11 | 查看 Spending controls 和 Advanced controls | 显示 USD、No automatic reset、已花费/预留/可用；组共享金额上限与每位会员次数分开 | 待人工填写 |
| Q12 | 在主聊天框输入下方 Luna 指令 | 先显示明确对象及旧→新值；点 Confirm change 才保存；产生回执，设置页同步 | 待人工填写 |
| Q13 | 确认成功后刷新聊天 | 保留成功回执；旧预览不再提供重复确认；可点 Open settings | 待人工填写 |
| Q14 | 在主聊天输入下方歧义指令 | 追问修改哪个额度，不擅自修改 | 待人工填写 |
| Q15 | 普通会员打开 Activity 的用量卡片 | 显示本人的实际额度与来源；没有老板管理入口；额度不足时显示原因和可恢复时间 | 待人工填写 |

可以直接复制的英文指令：

```text
Set the studio-wide Luna hourly limit to 500 calls.
Set the studio-wide Luna hourly limit to 100 calls.
Set the studio-wide Gemini hourly limit to 200 calls.
Set Default to 200 calls per member per day.
Give Demo Member 300 calls per day.
Restore Demo Member's group allowance.
Show members with custom allowances.
Set my owner daily allowance to 200 calls.
Set the initial account allowance to 200 calls per day.
Increase the limit to 500.
```

`Default`、`Demo Member` 要替换成页面上实际存在的组名或会员姓名。重名时先选择正确对象。`Increase the limit to 500.` 是歧义验收指令，应追问对象。

修改次数上限不会提高 OpenAI/Google 平台的限流或余额。减少到低于已用次数时，已有记录仍保留，后续调用等待滚动窗口释放容量。验收完请将临时修改恢复到原值。
