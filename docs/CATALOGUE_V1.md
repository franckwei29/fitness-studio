# Fitness Studio V1：体重、动态目录与 Profile

本次按全新数据库实施。现有本地数据库未删除、未执行本次迁移；验证使用独立 Compose 项目 `fitness-expanded-test`。

## 已实现的行为

- Profile 首次确认必须提供有效的正数体重（kg，可含小数），没有默认体重。聊天可以先保存未完成资料；确认卡、表单、资料历史和 Coach 权限包含体重。改体重产生新资料版本，已有计划按原来的版本检查提示更新。
- `food_catalogue` / `exercise_catalogue` 是统一查询入口，基础数据与 AI 补充数据一起检索。启动通过 `app.bootstrap` 幂等导入；重复执行不会重复插入或覆盖外部缓存。
- 基础食物：AUSNUT 2023 食物营养档案 3,741 条。保存原始 F 编号、名称、状态和每 100 g 数据。能量由 kJ / 4.184 转 kcal，碳水采用不含糖醇的 available carbohydrate。种子采用 gzip，不把全目录写入提示词。
- 基础运动：99 项，包含力量、有氧、活动度和球类；保留中英文别名、分类、计量方式、器材建议、MET。目录外也可以自动补充。
- `resolve_food()` / `resolve_exercise()` 先做本地检索；未命中时，经现有调用网关先搜索，再结构化为固定字段，应用生成 ID 并存入数据库。再次请求复用本地缓存。标准化查询哈希和唯一约束避免相同查询并发重复插入；查询保留品牌、生熟状态，别名参与检索。首版不是语义实体合并系统，相同食物的不同描述仍可能形成不同条目。
- 计划生成前提取明确要求的项目（每次最多四个，受现有调用上限约束），检索后向模型提供常用候选、明确要求和原计划涉及条目。后端校验是否遗漏明确要求，不允许悄悄用其他项目替换。
- 器材用于推荐排序，不再因未登记泳池、球或器材而拒绝用户指定运动。力量训练保留组数／次数；计时运动显示分钟。
- 营养 = 每 100 g 数据 × 克数 / 100；运动预计总消耗 = MET × 已确认 kg × 活动小时数。只统计 `sets × seconds_per_set`，热身、冷身、休息和转换另列，避免重复计算。不是实际活动记录，也不把热量作为周期进步指标。
- 每个已保存计划包含使用条目的 `catalogue_snapshot`；训练同时保存体重、时长、MET、预计热量，饮食保存营养合计。展示优先使用快照，缓存更新不会改写旧计划。
- 来源 URL 可空，允许估算。字段、单位、有限有效数值仍须通过校验；缺失营养不是零。过敏和明确饮食限制保留；基础食物里仅原有十条有已知饮食注释，其余标记未知，有限制时会尝试补全或说明不能使用。

## 外部调用与失败边界

搜索使用配置的 OpenAI 模型，结构化使用当前默认模型；通常为 OpenAI。两个步骤以及明确需求提取都经过相同的权限、开关、限流、预算预留和结算。计费搜索按当前配置加计 US$0.01 工具费（优先使用工具用量元数据；打开网页不作为搜索计费），搜索内容 token 仍计入模型 usage；搜索预留包含模型输入上界。没有新增外部服务账号。

字段不合格且计费可确定时，结算失败调用后最多重试一次；失败的输出不会入库或被当作成功。网络中断、计费未知时保留现有 usage reconciliation 机制，不为重试绕过预算或释放未知调用。额度不足仍按现有等待／恢复机制处理。最终目录失败会给用户明确说明；不增加人工目录审核门槛。

提取请求本身仍需要一次 AI 调用；“命中缓存不再调用外部补充”不代表整个聊天或计划生成完全不调用模型。四个目录外项目可能消耗较多调用，现有每请求上限仍生效。

## 初始化与重建

常规容器启动自动执行 `alembic upgrade head` 和 `python -m app.bootstrap`。新迁移为 `j261101a001`。无需联网下载数据才能启动。

在已配置后端环境中可以重复执行：

```sh
alembic upgrade head
python -m app.bootstrap
```

维护基础食物数据时，先下载官方 XLSX，再在仓库根目录运行：

```sh
python3 backend/scripts/import_ausnut.py /absolute/path/to/AUSNUT-2023-Food-nutrient-profiles.xlsx
```

转换脚本校验条目数、数值和原始 ID，生成 `foods.seed.json.gz` 与带原文件 SHA-256 的 manifest。此命令用于构建种子；不是全量外部自动同步。运动种子为 `backend/app/data/exercises.seed.json`。

数据出处：[FSANZ AUSNUT 数据文件](https://www.foodstandards.gov.au/science-data/food-nutrient-databases/ausnut/data-files)，署名 Food Standards Australia New Zealand，CC BY 4.0。原始文件、换算依据与校验和保存在 manifest；运动参考或估算标记保存在各条目：`met_basis` 保留具体参考活动及代码，或明确说明采用类别平均估算。

## Profile 视觉

基本资料、训练偏好、健康问答、确认四区；健康选择为并排可点击卡片，绿色选中态与勾选反馈，保留原生 radio 和键盘操作。未知问题不默认 No，进度按真实已回答数计算。共享弹窗固定头部、单一内容滚动区；修复 checkbox/radio 被通用 input 宽度覆盖的问题。

## 验证记录（2026-11-01）

- 隔离 MySQL/Redis 全新迁移及重复初始化成功，当前数据库不受影响。
- 后端回归：394 项通过；本次修改的 Python 文件 lint 通过，`git diff --check` 通过。
- 专项覆盖：体重无效／缺失、本地牛排／游泳／篮球、两步补充、再次缓存命中、并发去重、失败次数、预算拒绝、未知饮食信息、历史快照、计算不重复计时。
- 后端端到端：牛排／游泳／篮球从检索进入已校验草稿，确认之前不激活，用户确认后正常激活；测试模型输出为明确的模拟数据。
- Playwright：92 项通过，包含手机／桌面弹窗、键盘选择、单滚动区、确认反馈及共享弹窗回归。前端构建通过。
- 真实 OpenAI：`Cooked teff grain with water, no added oil` 和 `Underwater hockey recreational` 都成功补充；各两次调用均 Settled，重复解析均缓存命中。记录费用分别 US$0.011343、US$0.010858，合计 US$0.022201。
- 真实 API 只验证补充和缓存；整个计划确认流程由自动化集成测试验证。返回数据仍为估算，未声称营养或 MET 已获独立准确性认证。

复跑少量真实调用的脚本为 `backend/scripts/verify_catalogue_live.py`，仅接受 `fitness_expanded_test` 数据库，要求显式启用现有模型开关和足够预算；已有缓存时不会再次支付补充调用。私有凭据不写入仓库。

## 99 项运动清单

| 运动 | 中文／别名 | 类别 | MET | 标记 |
| --- | --- | --- | --- | --- |
| Sit to stand | 坐下再站起 | strength | 2.8 | Estimate |
| Supported mini squat | 扶椅小幅深蹲 | strength | 3.5 | Estimate |
| Supported calf raise | 扶椅提踵 | strength | 3.5 | Estimate |
| Supported sideways leg lift | 扶椅侧抬腿 | strength | 3.5 | Estimate |
| Standing leg extension | 扶椅站姿后抬腿 | strength | 3.5 | Estimate |
| Bodyweight squat | 自重深蹲 | strength | 3.5 | Estimate |
| Reverse lunge | 后撤弓步 | strength | 3.5 | Estimate |
| Forward lunge | 前跨弓步 | strength | 3.5 | Estimate |
| Split squat | 分腿蹲 | strength | 3.5 | Estimate |
| Step-up | 踏台上步 | strength | 3.5 | Estimate |
| Glute bridge | 臀桥 | strength | 3.5 | Estimate |
| Bodyweight calf raise | 自重提踵 | strength | 3.5 | Estimate |
| Goblet squat | 高脚杯深蹲 | strength | 3.5 | Estimate |
| Barbell back squat | 杠铃后蹲 | strength | 3.5 | Estimate |
| Barbell Romanian deadlift | 杠铃罗马尼亚硬拉 | strength | 3.5 | Estimate |
| Dumbbell Romanian deadlift | 哑铃罗马尼亚硬拉 | strength | 3.5 | Estimate |
| Dumbbell lunge | 哑铃弓步 | strength | 3.5 | Estimate |
| Barbell hip thrust | 杠铃臀推 | strength | 3.5 | Estimate |
| Leg press | 器械腿举 | strength | 3.5 | Estimate |
| Seated leg extension | 器械腿屈伸 | strength | 3.5 | Estimate |
| Seated leg curl | 坐姿腿弯举 | strength | 3.5 | Estimate |
| Hip abduction machine | 髋外展器械 | strength | 3.5 | Estimate |
| Resistance-band lateral walk | 弹力带侧步 | strength | 3.5 | Estimate |
| Wall press-up | 靠墙俯卧撑 | strength | 3.5 | Estimate |
| Incline push-up | 上斜俯卧撑 | strength | 3.5 | Estimate |
| Push-up | 俯卧撑 | strength | 3.5 | Estimate |
| Dumbbell bench press | 哑铃卧推 | strength | 3.5 | Estimate |
| Barbell bench press | 杠铃卧推 | strength | 3.5 | Estimate |
| Chest press machine | 坐姿推胸器械 | strength | 3.5 | Estimate |
| Dumbbell shoulder press | 哑铃肩上推举 | strength | 3.5 | Estimate |
| Shoulder press machine | 器械肩推 | strength | 3.5 | Estimate |
| Dumbbell lateral raise | 哑铃侧平举 | strength | 3.5 | Estimate |
| Cable triceps pushdown | 绳索三头肌下压 | strength | 3.5 | Estimate |
| Light dumbbell biceps curl | 轻哑铃二头肌弯举 | strength | 3.5 | Estimate |
| One-arm dumbbell row | 单臂哑铃划船 | strength | 3.5 | Estimate |
| Seated cable row | 坐姿绳索划船 | strength | 3.5 | Estimate |
| Lat pulldown | 高位下拉 | strength | 3.5 | Estimate |
| Assisted pull-up | 辅助引体向上 | strength | 3.5 | Estimate |
| Pull-up | 引体向上 | strength | 3.5 | Estimate |
| Resistance-band row | 弹力带划船 | strength | 3.5 | Estimate |
| Cable face pull | 绳索面拉 | strength | 3.5 | Estimate |
| Dumbbell hammer curl | 哑铃锤式弯举 | strength | 3.5 | Estimate |
| Reverse fly machine | 反向飞鸟器械 | strength | 3.5 | Estimate |
| Dead bug | 死虫式 | strength | 3.5 | Estimate |
| Bird dog | 鸟狗式 | strength | 3.5 | Estimate |
| Crunch | 卷腹 | strength | 3.5 | Estimate |
| Cable Pallof press | 绳索抗旋转推 | strength | 3.5 | Estimate |
| Kettlebell deadlift | 壶铃硬拉 | strength | 3.5 | Estimate |
| Forearm plank | 前臂平板支撑 | strength | 3.5 | Estimate |
| Side plank | 侧平板支撑 | strength | 3.5 | Estimate |
| Farmer carry | 农夫行走 | strength | 3.5 | Estimate |
| Outdoor walking | 户外步行 | cardio | 5.0 | Estimate |
| Brisk outdoor walking | 户外快走 | cardio | 5.0 | Estimate |
| Outdoor jogging | 户外慢跑 | cardio | 5.0 | Estimate |
| Outdoor running | 户外跑步 | cardio | 5.0 | Estimate |
| Hiking | 徒步 | cardio | 5.0 | Estimate |
| Treadmill walking | 跑步机步行 | cardio | 5.0 | Estimate |
| Treadmill running | 跑步机跑步 | cardio | 5.0 | Estimate |
| Stationary cycling | 固定健身车 | cardio | 5.0 | Estimate |
| Outdoor cycling | 户外骑行 | cardio | 5.0 | Estimate |
| Elliptical trainer | 椭圆机 | cardio | 5.0 | Estimate |
| Rowing ergometer | 划船机 | cardio | 5.0 | Estimate |
| Stair climber | 登阶机 | cardio | 5.0 | Estimate |
| Step aerobics | 踏板有氧 | cardio | 5.0 | Estimate |
| Jump rope | 跳绳 | cardio | 11.0 | Estimate |
| Low-impact aerobics | 低冲击有氧操 | cardio | 5.0 | Estimate |
| Aerobic dance | 有氧舞蹈 | cardio | 5.0 | Estimate |
| Pool swimming | 泳池游泳 | cardio | 6.0 | Estimate |
| Water walking | 水中步行 | cardio | 4.8 | Estimate |
| Water aerobics | 水中有氧 | cardio | 5.5 | Estimate |
| Standing calf stretch | 站姿小腿拉伸 | mobility | 2.3 | Estimate |
| Standing quadriceps stretch | 站姿股四头肌拉伸 | mobility | 2.3 | Estimate |
| Seated hamstring stretch | 坐姿腘绳肌拉伸 | mobility | 2.3 | Estimate |
| Chest-opening stretch | 胸部打开拉伸 | mobility | 2.3 | Estimate |
| Kneeling hip-flexor stretch | 跪姿髋屈肌拉伸 | mobility | 2.3 | Estimate |
| Shoulder circles | 肩部绕环 | mobility | 2.3 | Estimate |
| Supported single-leg balance | 扶椅单腿平衡 | mobility | 2.3 | Estimate |
| Badminton | 羽毛球 | ball_sport | 5.5 | Estimate |
| Tennis | 网球 | ball_sport | 4.0 | Estimate |
| Table tennis | 乒乓球 | ball_sport | 4.0 | Estimate |
| Squash | 壁球 | ball_sport | 4.0 | Estimate |
| Pickleball | 匹克球 | ball_sport | 4.0 | Estimate |
| Basketball | 篮球 | ball_sport | 7.5 | Estimate |
| Football (soccer) | 足球 | ball_sport | 7.0 | Estimate |
| Futsal | 五人制室内足球 | ball_sport | 7.8 | Estimate |
| Volleyball | 排球 | ball_sport | 4.0 | Estimate |
| Beach volleyball | 沙滩排球 | ball_sport | 4.0 | Estimate |
| Netball | 英式无板篮球 | ball_sport | 4.0 | Estimate |
| Cricket | 板球 | ball_sport | 4.0 | Estimate |
| Baseball | 棒球 | ball_sport | 4.0 | Estimate |
| Softball | 垒球 | ball_sport | 4.0 | Estimate |
| Golf | 高尔夫球 | ball_sport | 4.0 | Estimate |
| Touch rugby | 触式橄榄球 | ball_sport | 4.0 | Estimate |
| Australian rules football | 澳式足球 | ball_sport | 4.0 | Estimate |
| Field hockey | 草地曲棍球 | ball_sport | 4.0 | Estimate |
| Team handball | 手球 | ball_sport | 4.0 | Estimate |
| Water polo | 水球 | ball_sport | 4.0 | Estimate |
| Ten-pin bowling | 保龄球 | ball_sport | 3.8 | Estimate |
| Billiards | 台球 | ball_sport | 2.5 | Estimate |
