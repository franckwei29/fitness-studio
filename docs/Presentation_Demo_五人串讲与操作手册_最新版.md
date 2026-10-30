# Fitness Studio｜五人接力 Demo 串讲与操作手册

更新：2026-10-31。说明和操作用中文；复制到系统的输入、表单内容、建议口播用英文。

**本稿取代旧版五人操作稿的台前主线。**沿用 Victor → Oliver → Renee → 魏元康 Yuankang → 浩然 Haoran；元康改为模型比较、老板确认发布和多人分组，浩然改为按业务描述查询实际运行和管理额度。这里只编排演示，不修改系统配置或替任何老板执行决定。

本稿已对照当前源码和现有演示文档；本次没有运行全部英文 Prompt，也没有新调用付费模型。所有“预期结果”需要彩排验证，不能当成已完成的运行证明。

## 1. 用一句话串起五个人

> Alex 不知道怎么开始健身。AI 工作室先帮助他确认资料，再按他的条件制定训练和配套饮食。服务跑起来后，老板想试用另一个 Coach 模型：系统提供对比结果，老板决定是否小范围发布、邀请哪些会员试用。之后老板只需说“刚才那位会员的训练怎么样了”，系统帮助定位记录、解释结果和费用，老板再决定怎样控制用量。

这是一个同时服务会员和工作室老板的系统：前三人展示“把服务交付给会员”，后两人展示“让老板管理 AI 服务”。会员确认计划、老板确认发布是不同的业务决定。

| 全场时间 | 同学 | 本段核心问题 | 接给下一人的结果 |
|---|---|---|---|
| 00:00–02:00 | Victor | 怎么把聊天变成可信资料？ | Alex 已确认资料、可以开始规划 |
| 02:00–04:00 | Oliver / Aowei | 怎么生成符合条件的训练？ | 会员认可训练方向，开始营养阶段 |
| 04:00–06:00 | Renee / Shu-Chi | 怎么让饮食与训练配套？ | 会员核对后启用匹配的方案 |
| 06:00–08:00 | Yuankang | 新模型值不值得试、给谁试？ | 比较结果、老板发布决定、会员分配回执 |
| 08:00–10:00 | Haoran | 会员实际服务怎样、怎样控制用量？ | 实际运行反馈、费用说明、额度确认回执 |
| 10:00–12:00 | 团队 | 技术为什么有价值、如何迭代？ | 技术总结与真实 Agile 证据 |

每个人的两分钟包含口播、点击、等待和交接。详细步骤用于练习，不是要求台上逐字念完。

### 老板端统一规则

1. 老板提供姓名、组名、角色、最近一次等业务线索，不输入系统编号。
2. 系统先查找，反馈可识别的候选，例如“Alex Demo 的最近一次训练服务，时间、状态”。
3. 有歧义时，老板根据系统展示的候选选择；系统没有查到就说明没有查到，不猜。
4. 详情通过卡片或按钮携带上下文打开。内部编号可以存在，老板不用读、记、复制或填写。
5. 查询是只读；修改必须先看范围与结果预览，再点击确认；最后看真实回执。
6. 不把“最新一条记录”直接等同于“会员训练”。老板自己的查询也可能产生运行和费用记录。

## 2. 上台前准备：把等待放到彩排，不把假数据当真实效果

### 2.1 窗口、账号与统一人物

- **窗口 A：Member，Alex Demo。**独立浏览器配置或无痕窗口，避免与 Owner 串登录。
- **窗口 B：Owner。**元康与浩然始终保留同一条 Assistant 会话；复杂操作可打开对话内面板。
- 另准备已有会员 **Jamie Demo**，用于展示一次分配多个人。名字是演示建议，必须真实存在；实际名字不同就在所有台前输入中统一替换。
- 当前本机演示入口是 `http://127.0.0.1:8091/`；换机器按实际部署地址打开。
- Alex 的开场账号应尚未完成首次建计划。Jamie 无须现场重新入会。不要演示注册、登录或输入密钥。

### 2.2 Alex 的固定虚构测试资料

| 字段 | 输入 |
|---|---|
| Age | `24` |
| Training experience | `Beginner` |
| Main goal | `Build basic strength and a consistent exercise routine` |
| Available days per week | `3` |
| Minutes per session | `30` |
| Available equipment | `Stable chair` |
| Dietary restrictions | `milk` |
| Prescribed exercise programme | `No` |
| Prescribed nutrition programme | `No` |
| Medical supervision | `No` |
| Pregnancy / postpartum | `No` |
| Recovering from surgery / acute injury | `No` |
| Resolved past injury | `No` |

这些是虚构人物资料，不代表组员健康状况。器械和限制用当前目录支持的值。

### 2.3 本轮主线采用的服务设置

为了给元康完整两分钟讲模型治理，**前三段不再把“等待老板审核计划”作为交接点**。

提前在演示环境核对 Default：Training plans、Nutrition plans 开启；本轮首次方案采用不要求额外 Owner 计划审核的配置；会员最后确认仍然保留。若需改配置，提前通过已有配置提案、批准、执行流程完成，不能在会员任务运行途中改组规则。

若演示环境必须保留计划审核，则 Renee 展示真实的待审核状态，再切换到提前批准的有效检查点完成会员最终确认，并明确说明。不要临场绕过审核，也不要重新占用元康整段做计划审批。

Default、Canary 都沿用已有组，不新建组。当前每个会员只属于一个服务组；移动会员替换原来的组归属。组包含模型、服务能力和审核等配置，不只是标签。

### 2.4 提前准备的四类检查点

| 检查点 | 准备内容 | 上台如何说明 |
|---|---|---|
| 会员流程 | 同一真实测试会话的资料、训练草稿、营养草稿与最终结果 | 实时请求慢时展示彩排记录，不冒充实时生成 |
| 模型比较 | 当前实际 Coach 与可用 Gemini，使用相同完整指令和固定案例，已完成真实评测 | “This comparison was run before the demo.” |
| 发布决策 | 对应比较的有效发布提案；现场确认前重新核对状态、范围和人数 | 提案未发布时不说已经生效 |
| 运营证据 | 已发布试验版本下真实完成的会员服务及其可点击入口 | 若为彩排记录，明确说 recorded rehearsal trial |

**注意时间顺序：**新建发布后不可能天然已有“这次发布”的历史运行。元康现场发布新记录时，浩然可以先查询它，展示“尚无运行”；随后明确切到提前试用的另一条真实发布记录查证据。不能把另一条发布记录下的任务冒充刚才这一条。

十分钟最稳妥的安排：前三人展示真实检查点并现场执行关键确认；元康展示预运行比较，现场做有效发布/分配；浩然用提前说明的真实试用记录演示查询，现场做额度确认。不在十分钟内等待完整双模型测试和观察期。

提前验证 provider、worker、预算和调用额度；新安装仍需配置有效凭据并开启模型调用。没有已完成的真实评测，就只展示准备流程，不声称评测成功或发布成功。

## 3. Victor｜入会资料：从聊天到本人确认（00:00–02:00）

### V1｜介绍主角并输入资料（00:00–00:35）

窗口 A 保持 Alex 的 Assistant。开场口播，不发送：

> “Fitness Studio supports both members and the studio owner. I will start with Alex, a beginner who needs a practical training and meal plan.”

在消息框输入并点击 **Send message**：

```text
Hi, I would like to join the studio. Please help me complete my intake first.
I am 24 years old and a beginner. I want to build basic strength and a consistent exercise routine. I can train 3 days per week, for up to 30 minutes each time. I have a stable chair.
My dietary restriction is milk. Exclude milk and foods containing milk.
I do not require a prescribed exercise or nutrition programme. I do not need medical supervision. I am not pregnant or in postpartum recovery. I am not recovering from surgery or an acute injury, and I have no resolved past injury to review.
Please summarise my details for me to check. Do not create a plan yet.
```

等待系统反馈资料摘要和待确认内容。不要把“AI 已理解”说成“入会已经批准”。

### V2｜核对、修正并确认（00:35–01:35）

1. 打开 **My profile**；若无表单，点击 **Start my intake** 或已有资料的 **Edit my details**。
2. 对照第 2.2 节逐项检查；识别错误就在表单修正。
3. 让老师看到 3 天、30 分钟、器械和 milk，而不是逐项读完所有问题。
4. 勾选 **I have reviewed these details and confirm they are accurate.**
5. 点击 **Confirm my details** 一次。
6. 核对会员状态 **Accepted**，或明确的资料已确认、可以规划提示。没有成功状态就不进入“已通过”的讲述。

口播：

> “The AI proposes structured information. Alex can correct it, and the server checks admission rules after confirmation.”

### V3｜交给 Oliver（01:35–02:00）

关闭资料面板，或点击 **Back to conversation**；保留原会话。

> “Alex’s details are now confirmed. Oliver will show how Coach turns these constraints into a practical training draft.”

**交接条件：**资料已确认；原会话仍在；没有临时改组设置。

## 4. Oliver｜训练：生成、看懂、认可方向（02:00–04:00）

### O1｜提出完整服务目标（02:00–02:25）

继续窗口 A，不新开会话。输入：

```text
Please create my first training and meal plan together using my confirmed profile.
Start with a simple beginner training plan for 3 days per week, no more than 30 minutes per session, using my stable chair and supported exercises.
Show me the training draft first. Wait for me to accept the training direction before preparing the meals. The meals must exclude milk and foods containing milk. Do not activate the plans until I confirm the final update.
```

必须明确请求 training and meal plan together；仅要训练可能走独立训练路径，不能保证下一段有营养协作。

### O2｜查看训练草稿（02:25–03:25）

1. 等待训练草稿卡；若超出等待预算，按第 9 节切到真实检查点。
2. 从卡片打开训练草稿详情，只看本次最新草稿。
3. 指出每周安排、动作、组数/次数、休息，以及单次总时长。
4. 用 Alex 的条件解释：初学者、3 天、最多 30 分钟、已有器械。
5. 返回原会话，不点旧版本卡片。

口播：

> “Coach uses Alex’s confirmed time and equipment constraints. The server checks the exercise catalogue and calculated duration before presenting a usable draft.”

### O3｜认可方向，接给营养（03:25–04:00）

点击最新卡片 **Continue with this training** 一次；观察后续营养阶段启动。

> “Accepting this draft moves the workflow forward; it does not silently activate a plan. Renee will now show the meals linked to this training.”

**可选修订，不默认加入两分钟主线：**点击 **Request training changes**，在 **Changes to this draft** 输入下文，再点 **Send feedback**。等待新草稿并重新核对后才能认可。

```text
Make the explanation easier for a beginner to follow. Keep 3 sessions per week, the same available equipment, and the 30-minute limit. Do not change my meal requirements.
```

## 5. Renee｜营养：配套方案与最终交付（04:00–06:00）

### R1｜接住同一训练版本（04:00–04:25）

不重新发起一份无关食谱。使用上一段认可训练后产生的营养阶段。

> “Nutrition continues from the training Alex has just accepted. It is part of the same update, rather than an unrelated meal suggestion.”

### R2｜查看饮食内容（04:25–05:10）

1. 打开当前营养草稿详情；若先出现 **Review meal suggestions**，先点击进入实际建议。
2. 指出一餐里的食物、克重，以及每日营养合计。
3. 指出 milk 限制在结果中的实际处理。若发现不符合，不继续宣称通过。
4. 解释营养合计由目录数据和份量计算，不把模型文本当作计算依据。
5. 关闭详情返回当前流程。

口播：

> “The plan uses supported foods and quantities. Nutrient totals are calculated from catalogue data, while dietary restrictions and the linked training version are checked before activation.”

### R3｜会员最终确认（05:10–05:40）

1. 点击 **Continue with these meals**。
2. 按本轮预先核实的无额外 Owner 计划审核配置，进入 **Review your final update**。
3. 点击 **Review final changes**，确认是这次训练与饮食。
4. 返回卡片，点击 **Confirm update**。
5. 看到 **Update complete** 后，再打开 **My plans** 展示当前方案；以实际 **In use** 等状态为准。
6. 若实际出现 **Waiting for studio review**，按准备章节处理，不谎称已启用。

### R4｜从会员价值转到老板价值（05:40–06:00）

> “Alex now has a confirmed training and meal plan. Next, we switch to the owner: how can the studio improve its AI service without changing everybody’s experience at once?”

切到窗口 B，交给元康。前三段至此闭环，不再把计划审批塞进元康的模型段。

**可选饮食修订：**仅彩排或答辩使用 **Request meal changes** → 输入下文 → **Send feedback** → 等待新草稿。不得为了多展示按钮挤占最终确认。

```text
Make the meals simpler to prepare, using supported foods. Keep the milk restriction and the accepted training plan unchanged. Show the revised meals before activation.
```

## 6. Yuankang｜模型治理：看结果、试用、选会员（06:00–08:00）

### Y0｜准备阶段的完整输入：不上台等待跑完

在 Owner Assistant 输入：

```text
Read the current Coach configuration for Canary and the supported models. Prepare a comparison with an available Google Gemini model, using the same full Coach instructions and the same test cases. Use the current-model comparison workflow. Do not change any group settings. Show the proposal and wait for my confirmation. Reply in English.
```

若系统给出多个 Gemini 选项，按实际列表选择、回显完整名称。不要让老板填写不熟悉的内部编号，也不要把不可用模型写死。

核对 **Compare current model** 提案 → **Confirm and run** → **Open current record** → **Start N-call comparison** → 等待结果。准备比较本身不是已经运行，更不是发布。真实模型对比在彩排时完成。

### Y1｜展示老板问题与真实比较（06:00–06:45）

口播：

> “The owner wants to know whether Gemini is worth trying for Coach. We ran this comparison before the demo using the same instructions and test cases.”

1. 从会话中已准备的比较卡片打开详情。
2. 指向实际两个模型名。当前确为 Luna 才说 Luna；不说 Baseline/Candidate，不混用不同模型名称。
3. 只解释三项指标：

| 页面项目 | 给老板的意思 | 不能代表什么 |
|---|---|---|
| Plan checks | 这些测试中，计划满足已定义规则的情况 | 不是长期健身效果或人工质量评分 |
| Average wait | 本次记录里生成一次大概要等多久 | 不保证所有用户端到端响应都一样快 |
| Average cost per generation | 本次测试平均生成一次的费用 | 不是月账单；缺失费用不等于零 |

4. 展开 **Compare plan example**，指同一案例的一处真实表达差异，不逐句读答案。
5. 不写死哪一个胜出。是否试用由老板根据真实结果决定，不要求老板给每条答案打分。

可提前在对话中输入下面这段，现场展示已返回的解释：

```text
Explain the Coach comparison we just opened in plain English. Compare plan checks, waiting time and cost, and show one practical difference from the recorded answers. Say clearly if evidence or costs are incomplete. Do not publish anything. Reply in English.
```

### Y2｜说明组与确认发布（06:45–07:20）

> “Default keeps its existing configuration. Canary is our existing trial group. We can limit the change to that group before considering wider use.”

1. 在比较详情点击 **Prepare Canary release**。
2. 点击 **Review release and confirm**。
3. 看目标 **Canary**、受影响会员人数、新模型和保留版本。
4. 看提案是 **Pending Approval**；到此服务尚未改变。
5. 老板愿意试用才点击 **Confirm and publish to Canary**。
6. 核对成功回执，说明“已发布、正在观察”。观察期和最低完成运行数按实际显示，不能假装两分钟已观察完。

若不试用：点 **Keep current model**，展示已保存决定和提醒消除，再跳过发布、以已有真实试验记录接浩然。不可为凑故事宣称不合格证据也能发布。

### Y3｜一次分配多名已有会员（07:20–07:50）

在同一会话工具条打开 **Members** 面板：

1. 展开 **Assign members to a service group**。
2. 勾选 **Alex Demo**、**Jamie Demo**。人数由实际选择决定，不限一名；已在 Canary 的会员不算新转入。
3. **Destination group** 选择 **Canary**。
4. **Reason** 输入：

```text
Try the evaluated Coach model with these selected members in Canary.
```

5. 点击 **Review assignments**，核对每人的原组 → Canary，以及目标 Coach 模型。
6. 点击 **Confirm member assignments** 一次。
7. 核对保存人数；关闭面板，回到同一会话。

解释：每个人当前只有一个组；转组后新任务采用目标组规则；已有计划不会自动重生成；已发生用量和个人额度覆盖不清空。如果源组与 Canary 的审核、能力规则也不同，应一起说明。

对话式替代输入（台上选面板或聊天其中一条，不重复执行）：

```text
Find Alex Demo and Jamie Demo and show their current service groups. Prepare to move both to Canary for the Coach trial. Show both names, their current groups and the destination rules. Do not save until I confirm. If either name is ambiguous, show me the matches first. Reply in English.
```

检查名单后才点击对应 **Confirm and run**；未找到的人不能默默忽略。

### Y4｜交接浩然（07:50–08:00）

> “The owner has decided which model to trial and who should join. Haoran will now show how the owner finds actual member activity and controls usage without memorising technical identifiers.”

**准备运营证据时才做的会员输入：**转组之后，用对应会员账号提交新的请求，等待真实执行，给浩然留真实试用结果。台上不强行等待它完成。

```text
Create a training plan using my confirmed profile and available equipment. Explain why it fits my schedule. Show me a draft before activation.
```

## 7. Haoran｜运营：老板模糊提问，系统反馈，再推进（08:00–10:00）

本段不输入、不口播、不要求记忆任何 Request ID。老板只知道会员姓名、试用组和业务大意。系统负责内部定位。

### H1｜先说老板记得的事（08:00–08:20）

继续窗口 B 的同一 Assistant。输入：

```text
I remember Alex Demo was trying the Coach service. Find Alex and show the recent training-related activity, with the time and current status, so I can recognise the right one. Do not change anything. If there is more than one possible match, show me the choices first. Reply in English.
```

**预期：**系统查会员，再查其运行记录，反馈可识别的时间、类型、状态和证据入口；不是反过来让老板提供编号。当前工具主要能读会员身份、组和运行元数据，不应声称能靠全文搜索会员私密聊天定位。

老板用词模糊，系统不能保证一次猜中。真正展示的是“帮助老板缩小范围”，不是神奇地知道老板心中是哪一条。

### H2｜看系统反馈后，再选中实际记录（08:20–08:45）

**系统只有一个可靠候选时：**点击它提供的运行证据卡片。也可发：

```text
Open the training activity you just found for Alex. Tell me whether it completed and which Coach model actually ran. If that was not a Canary trial, say so. Do not change anything. Reply in English.
```

**出现多条时：**先读系统展示的时间和状态，再点击对应候选。不要把下面相对时间句子机械发出；只有列表确实支持这一选择时才发：

```text
Open the most recent completed training activity for Alex from the list you just showed. Check whether it used the trial Coach model before describing it as a trial result. Reply in English.
```

如果同名会员不唯一，用系统展示的组/状态澄清或在 Members 中选定对应人；不让老板编内部编号。

**更稳定的卡片路径：**从元康的发布回执打开发布详情 → 点击 **View trial requests** → 系统按这条发布的实际组和版本查出会员服务 → 根据会员姓名、角色和状态点击 **Open execution evidence**。老板只点业务记录，编号由系统传递。

**如果刚发布还没有记录：**展示空列表，说明“尚未收到该发布下的会员运行”；随后明确切到提前准备的真实试验发布卡片，重复上述两次点击。口播：

> “This new release has no member runs yet. To show what the owner can inspect after real usage, I’ll open our recorded rehearsal trial.”

若先前已发布且本次展示的是历史发布回执，就从开场说清楚，不必假装又发布一次。

### H3｜把运行与费用翻译成老板能懂的反馈（08:45–09:10）

在证据面板只指出三件事：

1. **做完没有？**看真实任务状态；Completed 才说完成。
2. **谁做的？**看 Coach 的真实模型调用，不拿管理聊天使用的模型来代替。
3. **多少钱？**看这次会员服务已结算费用；有预留或未知就说明尚未确定。包含其他角色时，不能把整条请求费用全部说成 Coach 费用。

可选追问，时间紧就直接口播，不多等一次模型：

```text
For the member activity we just opened, explain the settled cost and any reserved or unknown amount in plain English. Tell me what is confirmed and what is still pending. Reply in English.
```

口播：

> “The owner starts with a member’s name, then selects the activity from the system’s feedback. The recorded calls show what actually ran. Reserved or unknown cost is not treated as free.”

这里是实际会员运行；元康的费用是固定测试案例，两者口径不同，不能直接拼成一个节省百分比。

### H4｜根据运行反馈管理用量（09:10–09:50）

关闭详情，回到原会话。输入：

```text
Show the current Canary group call allowance. Prepare to set its rolling 24-hour allowance to 100 calls per member who inherits the group rule. Keep personal overrides, existing usage and monetary budgets unchanged. Show the group and the proposed value, and wait for my confirmation. Reply in English.
```

1. 先看返回的当前值；如果已经是 100，本轮改用下方修正句，必须以实际反馈为依据：

```text
It is already 100. Prepare a change to 150 instead, with the same scope. Wait for my confirmation. Reply in English.
```

2. 核对预览：组为 Canary；每位继承组规则的会员；滚动 24 小时；100，或根据返回结果选择的 150。
3. 点击 **Confirm and run**。若走确定性额度卡并显示 **Confirm change**，核对相同范围后点击该按钮；不要因为按钮文字不同重复提交。
4. 看保存回执，再通过 **Open current record** 核对新值；如果是额度卡直接显示保存值，就在卡内核对。
5. 解释“不是整组共享 100 次，也不是每天午夜清零；一次聊天可能包含多次模型调用”。
6. 此步骤不增加美元预算、不清空历史用量、不覆盖个人例外。

### H5｜结束十分钟（09:50–10:00）

> “We have shown the full loop: member intake, coordinated plans, a controlled model trial, and owner-led operations. The owner uses business context, receives evidence, and confirms changes. We will now explain the technology and our Agile process.”

## 8. 最后两分钟：不再追加功能流程

| 时间 | 讲者建议 | 屏幕与口播重点 |
|---|---|---|
| 10:00–10:45 | 元康 | 一张架构图：React 交互、FastAPI 规则、专业 Agent、持久化与任务执行；强调“模型建议，服务端校验，用户确认” |
| 10:45–11:40 | 浩然 | 真实 Jira 看板、Sprint/任务分配和已有燃尽图；选一条实际需求到实现到验证的迭代证据 |
| 11:40–12:00 | Victor | 回到公司价值：会员得到连续服务，老板能控制 AI 服务、查看真实结果 |

技术总结口播示例：

> “Specialist agents handle different parts of the service. Structured outputs and server-side checks make their proposals reviewable. Persistent workflows preserve progress, while explicit confirmation controls changes to plans and AI service configurations.”

Agile 只用真实记录，不把本演示稿当成实际提交或开发贡献证据。分工是演讲与代码阅读边界，个人开发贡献仍按真实工作说明。

## 9. 现场异常处理与计时规则

### 模型回复慢

等待约 15 秒仍未返回，先讲已显示的业务机制；会挤占下一人的时间就切真实检查点。说明：

> “The live task is still running. I’ll use a completed rehearsal record to show the next stage within our demo time.”

切换时后续人员沿用该检查点的会员和版本链。不能把不同会员的训练、营养、发布拼成同一条实时记录。

### 查询不到或结果不唯一

- 先看系统候选，再缩小到姓名、角色、状态和时间。
- 同一对话仍未可靠定位时，用业务卡片/发布内试用列表选择；不临场找 UUID。
- 没数据就展示没数据。没有真实试用证据时，浩然可展示前三段 Alex 的实际服务运行，并说明它不是 Gemini 试用结果；再演示 Canary 的额度管理。

### 提案过期、配置改变或保存失败

读错误反馈，重新读取当前值并形成新预览。不连续点击旧确认；没有保存回执就不说完成。

### 评测不完整或结果不支持发布

如实展示未完成/失败状态，跳过发布。保留当前模型也是有效老板决定。未发布时不能用“刚才已切换”交接。

### 时间不够时删什么

优先删训练修订、饮食修订、第二次 AI 解释、详细输出逐句对比。保留每人一个可见结果和关键确认。最后两分钟不挪给模型等待。

## 10. 五人彩排勾选单

- [ ] 两个角色独立登录，投影不出现密码或密钥。
- [ ] Alex、Jamie 都是真实测试账号，所有输入姓名与界面一致。
- [ ] 当前模型名称已核对，不机械说 Luna。
- [ ] 前三段采用同一会员流程；计划审核策略事先固定。
- [ ] 训练与饮食都展示了实际内容，最终状态没有夸大。
- [ ] 元康的比较是真实运行；只有三个主要指标，无人工质量评分门槛。
- [ ] 发布必须老板点击；已有 Canary 会员受影响范围已说明。
- [ ] 多人分配显示完整名单；已有计划、个人额度和用量的含义已说明。
- [ ] 浩然从姓名/业务描述开始，所有编号由系统内部处理。
- [ ] 新发布无记录与历史试验记录已区分，不冒充现场实时结果。
- [ ] 额度是读当前值 → 预览 → 确认 → 回执，不是只展示一句聊天回复。
- [ ] 已按这份英文输入完整彩排；五段各不超过两分钟。
- [ ] 技术图与真实 Agile 证据已预开。

## 附录：核心代码文件，老师追问时再打开

链接相对本文件；不需要在台前记文件名或任何业务记录编号。

| 同学 | 主读文件 | 解释边界 |
|---|---|---|
| Victor | [intake_runtime.py](../backend/app/intake_runtime.py)、[admission.py](../backend/app/admission.py)、[rules.py](../backend/app/rules.py)、[Intake.tsx](../frontend/src/Intake.tsx) | 聊天提取、本人确认、规则准入 |
| Oliver | [coach_runtime.py](../backend/app/coach_runtime.py)、[training.py](../backend/app/training.py)、[WorkflowCard.tsx](../frontend/src/WorkflowCard.tsx) | 训练生成、目录与时长校验、认可训练方向 |
| Renee | [nutrition_runtime.py](../backend/app/nutrition_runtime.py)、[nutrition.py](../backend/app/nutrition.py)、[dietary_policy.py](../backend/app/dietary_policy.py)、[activation.py](../backend/app/activation.py) | 饮食生成、份量计算、匹配方案最终启用 |
| Yuankang | [version_admin.py](../backend/app/version_admin.py)、[evaluation_runtime.py](../backend/app/evaluation_runtime.py)、[agent_release.py](../backend/app/agent_release.py)、[member_admin.py](../backend/app/member_admin.py) | 当前模型对比、真实评测、确认发布、多人分组 |
| Yuankang UI | [Evaluations.tsx](../frontend/src/Evaluations.tsx)、[CurrentModelComparison.tsx](../frontend/src/CurrentModelComparison.tsx)、[Releases.tsx](../frontend/src/Releases.tsx)、[MemberAssignments.tsx](../frontend/src/MemberAssignments.tsx) | 模型卡片、发布按钮与人员确认预览 |
| Haoran | [owner_tools.py](../backend/app/owner_tools.py)、[operations.py](../backend/app/operations.py)、[trace_data.py](../backend/app/trace_data.py)、[call_limits.py](../backend/app/call_limits.py)、[budget.py](../backend/app/budget.py) | 从会员找到运行、证据和费用、额度保存 |
| Haoran UI | [Dashboard.tsx](../frontend/src/Dashboard.tsx)、[CallLimits.tsx](../frontend/src/CallLimits.tsx)、[OwnerTaskCard.tsx](../frontend/src/OwnerTaskCard.tsx) | 证据面板、额度卡、执行回执 |

共同接口：[workflows.py](../backend/app/workflows.py) 由 Oliver 讲训练阶段、Renee 讲营养和最终阶段；[model_gateway.py](../backend/app/model_gateway.py) 由元康讲版本选择、浩然讲调用和费用；[owner_agent.py](../backend/app/owner_agent.py) 和 [OwnerWorkPanel.tsx](../frontend/src/OwnerWorkPanel.tsx) 支撑后两人的同一对话工作方式。

每人准备解释一条链：**我点击/输入了什么 → 系统查什么 → 哪一步要确认 → 最后保存了什么**。这比背系统编号更能说明项目实现。
