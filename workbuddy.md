# WorkBuddy 开发对话记录

> 本文件用于核验「使用 WorkBuddy 开发 Skill」的联动活动奖励条件（3,000 积分）。
> 项目：**麦麦暗号厨房 / McSecret Kitchen**
> 开发工具：腾讯 WorkBuddy（https://www.workbuddy.cn）
> MCP：麦当劳中国官方 MCP Server（https://mcp.mcd.cn）

---

## 1. 需求梳理

**背景**：麦当劳程序员创意开发大赛，需基于麦当劳 MCP 开发创意 Skill，截止 2026-10-25 23:59。本项目是主项目（麦麦积分清仓官）之外的**并行备选作品**，用于分散风险。

**选题依据**：在通读麦当劳 MCP 全部 30 个 Tool 后注意到，官方版本日志 1.0.5 起「支持更换套餐内商品组合、部分餐品特制」，对应 `query-meal-detail` 返回套餐内每项的**可替换选项**。这层能力在官方 App 里是被动的（用户得自己点进套餐逐个试），没人把它做成主动枚举。

榜单上同类项目有 meal-puzzle（116 Star）、mctaste-lab（101 Star），说明方向有需求但尚未被做透，仍有空间。

---

## 2. WorkBuddy 中的开发过程（摘要）

**第 1 轮 · 能力挖掘**

- 逐个评估 30 个 MCP Tool，锁定 `query-meals` + `query-meal-detail` + `calculate-price` + `list-nutrition-foods` 这条组合链。
- 确认 `query-meal-detail` 的「可替换选项」是隐藏菜单的数据来源。

**第 2 轮 · 组合引擎实现**

- 用 `itertools.product` 对各组可替换项做笛卡尔积枚举。
- 关键约束：**跳过全默认组合**，否则输出的就是原版套餐，失去意义。

**第 3 轮 · Bug 修复（真实发生的问题）**

- 初次运行发现「穷鬼暗号」命中了「中薯条 + 中可乐」，即**原版套餐没被过滤掉**。
- 根因：比较时拿 option 的 dict 列表与 default 的字符串列表相比，`chosen == defaults` 恒为 False。
- 修复：先取出 `parts`（名称列表）再与 `defaults` 比较。修复后组合数从 43 降到 41，两个全默认组合被正确剔除。
- 同时改进精选逻辑：每个暗号改为在该类中按 (价格, 热量) 升序取最典型的一个，而非取枚举顺序里的第一个。

**第 4 轮 · 暗号命名设计**

- 设计 7 条命名规则（轻断食 / 穷鬼 / 加班续命 / 增肌 / 伪健康 / 早餐魂 / 自由派），把枯燥的组合表变成有梗的清单，增强传播性。

**第 5 轮 · 工程收尾**

- 补齐参赛五件套文件，`CONTEST_DECLARATION.md` 严格使用官方原文未改动。
- Token 仅通过环境变量 `MCD_MCP_TOKEN` 传入，符合信息安全声明。

**第 6 轮 · 真实环境联调（关键一轮）**

用户提供了真实 MCP Token，**全程只通过环境变量传入，未写入任何文件**。

- 首个坑：`query-meals` 不带参数直接返回 `code:400 缺少参数`。通过 `tools/list` 读 inputSchema 拿到确切必填项 —— `storeCode` / `orderType` / `beType`，而非靠猜。
- 第二个坑：`structuredContent` vs `content[].text`。与主项目同源问题，`content[].text` 是 Markdown 说明文档不是 JSON，修正 `unwrap()` 后解决。
- 连锁链路打通，且**不需要用户输入任何位置信息**：
  `delivery-query-addresses`（无参数）→ `delivery-query-stores`{addressId, beType:2} → `query-meals`{storeCode, beCode, orderType:2, beType:2} → `query-meal-detail`
- 真实套餐结构确认，可替换选项确实存在：
  ```
  "rounds":[{"name":"选择小食","choices":[
     {"name":"麦辣鸡翅2块","isDefault":1,"diffPrice":"+ ¥0"},
     {"name":"薄皮焦香V翅","isDefault":0,"diffPrice":"+ ¥2"}]}]
  ```
  只有 `choices` 多于 1 项的 round 才有替换空间；`diffPrice` 形如 `+ ¥2`，用正则提取数字。
- 真实运行结果：一次组合出 **2216 种**菜单上不存在的搭配，真实套餐名如「四拼小食可乐餐」「深夜吃鸡堡堡桶」「全明星双人分享餐 8 件套」。
- 一处被迫的产品调整：`list-nutrition-foods` 在无参数调用时返回空，拿不到热量。原暗号规则依赖 kcal 会全部误命中（kcal=0 时「≤150 kcal」恒真），因此把暗号规则改为基于**差价 + 替换数量 + 食材关键词**，热量只在有数据时才参与。

---

## 3. 关键技术决策

| 决策 | 原因 |
|---|---|
| 零第三方依赖（仅 stdlib） | 克隆即跑，降低使用门槛 |
| 笛卡尔积枚举 + 跳过全默认 | 保证输出全是「菜单上不存在」的搭配 |
| 套餐详情拉取限制在前 8 个 | 控制 MCP 调用次数，避开 600 次/分钟限流 |
| 暗号命名层 | 把数据表变成有梗的内容，利于分享传播 |
| 演示模式兜底 | 无 Token 者（含评审）也能立即看到效果 |
| 绝不自动下单 | 只做推荐，下单必须用户显式确认 |

---

## 4. 交付物

- `mcd_secret_kitchen.py` — MCP 客户端 + 组合引擎 + 本地服务（单文件）
- `web/index.html` — 前端单页
- `skill/SKILL.md` — WorkBuddy Skill 定义
- `MCP_INTEGRATION.md` — MCP 接入说明
- `CONTEST_DECLARATION.md` — 参赛声明（官方原文）
- `README.md` — 项目介绍

---

*本文件记录真实开发过程，由腾讯 WorkBuddy 协助生成。*
