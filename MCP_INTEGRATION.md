# MCP 接入说明

本项目基于**麦当劳中国官方 MCP Server** 构建，接入地址 `https://mcp.mcd.cn`，传输协议 **Streamable HTTP**，鉴权方式 `Authorization: Bearer <MCP_TOKEN>`。

Token 申请：<https://open.mcd.cn/mcp>（手机号登录后，控制台 → 激活 → 复制 Token）。

## 实际调用的 Tool

| # | Tool 名称 | 在本项目中的作用 | 业务价值 |
|---|---|---|---|
| 1 | `query-meals` | 拉取当前门店可售卖的餐品列表（餐品编码、分类、价格、标签） | 建立组合的原材料池 |
| 2 | `query-meal-detail` | 查询套餐组成，**以及每个组成项的可替换选项** | 项目的核心：可替换选项就是隐藏菜单的来源 |
| 3 | `calculate-price` | 对组合出的自定义搭配计算应付总价 | 让每个「暗号」都有真实价格，而不是臆测 |
| 4 | `query-nearby-stores` | 查询附近门店，确定当前门店上下文 | 不同门店菜单与供应状态不同 |
| 5 | `list-nutrition-foods` | 获取餐品营养成分数据（能量、蛋白质、脂肪等） | 为「轻断食暗号」「增肌暗号」提供热量依据 |

## 调用流程（真实链路，全程无需用户输入位置）

```
[1] delivery-query-addresses  （无参数）
      └─► addressId
   ▼
[2] delivery-query-stores  {addressId, beType:2}
      └─► storeCode, beCode
   ▼
[3] query-meals  {storeCode, beCode, orderType:2, beType:2}
      └─► categories[] → 每个分类的 meals[] → 餐品 code
   ▼
[4] 对前 12 个 code 调 query-meal-detail {storeCode, beCode, orderType:2, beType:2, code}
      └─► rounds[] → choices[]
          • choices 只有 1 项 = 没得换，跳过
          • isDefault=1 是当前默认项
          • diffPrice "+ ¥2" 是差价
   ▼
[5] 组合枚举：对有替换空间的 round 做笛卡尔积
      • 跳过「全默认」组合（那是原版套餐，不算隐藏菜单）
      • diff = Σ 差价
   ▼
[6] 暗号命名 → 排序输出
```

参数取值说明：`orderType=2`（外送）、`beType=2`（麦乐送到家）。这两个值来自 `tools/list` 的 inputSchema，不是猜的。

## 两个真实踩坑（值得记下来）

**坑 1：结构化数据在 `structuredContent`，不在 `content[].text`。**

麦当劳 MCP 的 `content[].text` 是一段给大模型看的 Markdown 说明文档，以 `# API Response Information` 开头，描述每个字段的含义，最后才附带完整响应。`json.loads(text)` 必然失败。真正的结构化数据在 `structuredContent.data` 里，标准封装为 `{success, code, message, datetime, traceId, data}`。

第一版代码直接解析 `content[].text`，结果所有积分都读到 0 —— 不是账号没积分，而是根本没取到数据。

**坑 2：`query-meals` 必须带门店参数，无参数调用返回 `code:400 缺少参数`。**

必填：`storeCode`、`orderType`、`beType`。而 `storeCode` 又要先从地址 → 门店两步拿到。本项目用 `delivery-query-addresses`（无参数）自动取用户已有地址，避免了让用户手动填经纬度。

### 营养数据是 TOON 格式，不是 JSON

`list-nutrition-foods` 无需入参，但它的 `data` 是 **TOON（Token-Oriented Object Notation）字符串**，官方为降低 Token 消耗特意如此设计：

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
  中薯条,null,1210,289,4,12,38,165,18
```

`[160]` 是记录数，`{...}` 是列名，之后每行一条、逗号分隔。直接 `json.loads` 或按数组找行都会得到空结果 —— 第一版就是这样误以为营养接口不可用。

本项目写了 `parse_toon()` 解析器，把 160 道餐品的热量建成 `{归一化名称: 千卡}` 映射，再回填到套餐的每个可替换项上，`轻断食暗号` 这类依赖热量的规则才得以成立。

单位辨析：`energyKj` 是千焦，`energyKcal` 是**千卡/大卡**（日常说的「热量」），二者是同一能量的两种单位（1 kcal ≈ 4.184 kJ，示例 308 kcal × 4.184 ≈ 1288 kJ，吻合）。本项目取 `energyKcal`。

餐品名跨表匹配做了归一化（去空格、连字符、括号），因此套餐里的「麦辣鸡翅2块」能匹配到营养表的「麦辣鸡翅-2块」。

## 字段兼容性处理

## 关于「可替换选项」这层能力

`query-meal-detail` 返回的套餐组成里，每一项都带有**可替换选项列表**（薯条可换玉米杯、可乐可换拿铁、可加双层牛肉等）。麦当劳官方版本日志里也明确提到 1.0.5 起「支持更换套餐内商品组合、部分餐品特制」。

这层能力在官方 App 里是被动的（你得自己点进套餐一个个试），本项目把它变成**主动枚举**：一次性把所有组合摊开，挑出有意思的那些并命名。菜单上不存在的搭配，其实一直都存在。

## 字段兼容性处理

MCP 各 Tool 返回结构存在版本差异，项目做了两层容错：

- `find_rows(obj)`：从任意嵌套结构中挖出列表，兼容 `data` / `items` / `meals` / `products` / `list` 等容器命名。
- `pick(row, *keywords)`：按键名关键词模糊取值，兼容 `mealCode` / `productCode` / `code` 等不同命名。

解析失败时会明确回落演示数据并提示原因，不会静默出错。

## 限流与错误处理

- 官方限制 **600 次/分钟**。项目对套餐详情的拉取限制在前 8 个套餐，单次最多约 10 次调用。
- `401` 表示 Token 无效或过期，会回落演示模式并提示。
- MCP 响应可能是 JSON 也可能是 SSE（`text/event-stream`），客户端两种都做了解析。

## 业务价值

对用户：把「我想吃点不一样的」变成一份可以直接报给店员的清单，还带暗号名和真实价格。

对麦当劳：把「套餐可定制」这层已有能力从被动选项变成主动玩法，提升客单价（加双层牛肉、换拿铁都是溢价项）与点餐趣味性。

## 接入 WorkBuddy

在 WorkBuddy 左侧栏【专家·技能·连接器】→【连接器】→【自定义连接器】→【配置 MCP】，填入：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_TOKEN"
      }
    }
  }
}
```

启用后直接对话即可，例如：「帮我用巨无霸套餐组合一个热量最低的搭配」。
