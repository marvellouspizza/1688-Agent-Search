---
name: 1688-identify-product-keywords
description: Use when an agent receives a Chinese invoice/procurement line item and needs to identify what the item is, parse specs, distinguish lookalikes, and generate structured 1688 search keywords without collecting seller links.
version: 1.0.3
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [1688, procurement, sourcing, search-keywords, chinese-commerce]
    related_skills: [1688-find-product-links, 1688-product-identification-search]
---

# 1688 Identify Product and Build Search Keywords

## Overview

Use this skill for the first stage of 1688 sourcing: turning a Chinese invoice/procurement line item into a clear product interpretation and practical search keywords.

This skill answers:

- “这是什么东西？”
- “它在 1688 上通常叫什么？”
- “我应该怎么拼搜索词？”
- “哪些词要带上，哪些坑要避开？”

It does **not** collect seller links or rank suppliers. For that, pass this skill's structured output into `1688-find-product-links`.

When preparing the handoff, always include a `link_search_request` object and explicitly require the downstream skill to preserve **both**:

- `seller_url`: the 1688 shop/store/company URL, e.g. `https://seller.1688.com/` or `https://shopxxxx.1688.com/`
- `product_url`: the direct offer/detail URL, e.g. `https://detail.1688.com/offer/123.html`

Do not collapse these into one generic `url` field; other agents need both links for procurement review.

## When to Use

Use when the user or another agent provides a procurement row like:

```text
发票种类 名称 规格型号 品牌 数量 单位
13%专票 袖标 9×10CM,印字安全员，带别针 500 个
```

Use for:

- Invoice/procurement item recognition
- Commodity/category explanation
- 1688 keyword generation
- Seller inquiry question drafting
- Preparing structured input for a seller/link-search agent

Do not use for:

- Finding 20 merchants or product links directly
- Negotiating with sellers
- Placing orders
- Verifying live inventory

## Input Contract

Accept either raw text or structured JSON.

### Raw text input

```text
发票种类 名称 规格型号 品牌 数量 单位
13%专票 盖板模具 塑料 720×400×60mm 500 个
```

### JSON input

```json
{
  "invoice_type": "13%专票",
  "name": "盖板模具",
  "spec": "塑料 720×400×60mm",
  "brand": "",
  "quantity": 500,
  "unit": "个"
}
```

## Output Contract

Prefer structured JSON when another agent will consume the result. For direct user-facing replies, use Chinese prose plus grouped keyword blocks.

### JSON output schema

```json
{
  "target_item": "string: concise normalized item name",
  "item_summary": "string: what it is in plain Chinese",
  "not_this": ["string: common confusions to avoid"],
  "parsed_fields": {
    "invoice_type": "string|null",
    "name": "string",
    "spec": "string|null",
    "brand": "string|null",
    "quantity": "number|string|null",
    "unit": "string|null"
  },
  "must_have": ["string: hard requirements from input"],
  "avoid": ["string: wrong variants to avoid"],
  "category_terms": ["string"],
  "keywords": {
    "precise": ["string"],
    "broad": ["string"],
    "scene": ["string"],
    "material_or_style": ["string"],
    "supplier": ["string"],
    "invoice_matching": ["string"]
  },
  "seller_questions": ["string"],
  "pitfalls": ["string"],
  "link_search_request": {
    "target_item": "string: pass-through name for 1688-find-product-links",
    "must_have": ["string: hard requirements to rank links"],
    "avoid": ["string: wrong variants to filter out"],
    "keywords": {
      "precise": ["string"],
      "broad": ["string"],
      "scene": ["string"],
      "supplier": ["string"]
    },
    "limit": 20,
    "required_output_fields": ["seller_name", "seller_url", "product_title", "product_url", "price", "location", "match_level"]
  }
}
```

## Workflow

### 1. Parse fields

Extract:

- 发票种类: e.g. `13%专票`
- 名称: core item name
- 规格型号: dimensions, material, wording, process, attachment, model
- 品牌: if present
- 数量 + 单位: affects wholesale/custom wording

### 2. Identify real product category

Do not merely repeat the invoice name. Infer the commercial category and common 1688 names.

### 2a. Preserve brand and model constraints

If `品牌` is present, or the user states that a model/spec belongs to a brand, treat that brand as a hard procurement constraint rather than a loose keyword. Also infer brand/host-machine constraints from `规格型号` and `备注/技术要求` when they name a host brand, model, equipment family, vehicle, pump, valve, sanitaryware, lighting, or electrical system.

Only put real brands, manufacturer names, accepted brand allow-lists, or host brand/model compatibility wording into `parsed_fields.brand`.

### 2b. 品牌字段污染识别 (Detect polluted brand fields)

Treat the source `品牌` column as polluted when it is actually a long specification, construction requirement, bundled-accessory description, service requirement, quantity/unit phrase, or acceptance note rather than a brand. Examples include wording with `每3米一节`, `配套`, `缝宽`, `深度`, `端头`, `含设计出图`, `钢板`, `26吨`, `13%专票`, or similar process/service details.

When the brand field is polluted:

- Do not copy it into `parsed_fields.brand`; set `parsed_fields.brand` to `null`.
- Merge the useful content into `spec`, `must_have`, `hard_gates`, and `included_item_requirements` as procurement constraints.
- Preserve those constraints in `link_search_request.target_item`, `link_search_request.must_have`, `link_search_request.hard_gates`, or `link_search_request.included_item_requirements`.
- Do not force the full polluted sentence into every keyword; keep keywords short and product-subject focused.

Examples:

| Invoice wording | Real category | Common 1688 names |
|---|---|---|
| 防撞桶，高1米左右，玻璃钢 | 道路交通安全设施 | 玻璃钢防撞桶、道路防撞桶、交通防撞桶、反光防撞桶 |
| 盖板模具，塑料，720×400×60mm | 水泥/混凝土预制盖板塑料模具 | 水泥盖板模具、混凝土盖板模具、排水沟盖板模具、电缆沟盖板模具 |
| 袖标，9×10cm，印字安全员，带别针 | 小号安全员袖标/臂章/胸章式标识 | 安全员袖标、安全员臂章、别针款臂章、红袖章 |
| 3060YG-24-10K 子弹头 | 工程钻具截齿 | 旋挖截齿、旋挖齿、子弹齿、钻齿 |
| 保安亭板房，3m×2m×2.8m | 门卫/安保用成品岗亭或可移动值班室 | 保安亭、岗亭、门卫室、值班室、成品岗亭、可移动岗亭、钢结构岗亭、彩钢保安亭 |

### 3. Distinguish lookalikes

State what it is **not** when confusion is likely:

- `盖板模具` is not finished `盖板`.
- `玻璃钢防撞桶` is not ordinary PE/滚塑/吹塑桶.
- `9×10cm 带别针袖标` is not a large arm-worn sleeve armband.
- Construction `子弹头` is not ammunition.
- `保安亭板房` is usually not a generic dormitory `活动板房` or `集装箱宿舍`; search as `保安亭/岗亭/门卫室/值班室`, then confirm whether the seller can build the requested dimensions and wall-panel configuration.

### 4. Build keyword groups

Generate these groups:

1. `precise`: exact item + key specs
2. `broad`: synonyms/common seller wording
3. `scene`: use case / project context
4. `material_or_style`: material, manufacturing process, style, attachment
5. `supplier`: 厂家/批发/定制/生产厂家 terms
6. `invoice_matching`: terms closest to invoice wording

For every keyword intended for 1688 text search:

- Use a short noun phrase, not a sentence or explanatory phrase.
- Prefer `采购主体 + 1 个补充词/定语`; either order is acceptable when it matches common 1688 seller wording.
- Keep the real product subject in the keyword. Infer professional product wording from road, bridge, construction, and jobsite procurement context when the source wording is colloquial.
- Do not include comma-separated descriptions, parenthetical explanations, brand/manufacturer wording, piled dimensions, quantity/unit, delivery, installation, inspection, or acceptance wording.
- Put required dimensions and checks into `must_have` or parsed fields instead of stuffing them into the primary text-search keyword.

### 5. Draft seller questions

Ask about:

- Size/dimension meaning
- Material
- Quantity price
- 13% 专票
- Delivery time and freight
- Photos/drawings/samples
- Customization if relevant

### 6. Prepare link-search handoff

If the user may ask for merchants or links, add a `link_search_request` object for `1688-find-product-links`.

The handoff must include:

- `target_item`
- `must_have`
- `avoid`
- grouped `keywords`
- `limit`, normally 20 if unspecified
- `required_output_fields`, including both `seller_url` and `product_url`

Example:

```json
{
  "link_search_request": {
    "target_item": "橡胶抽拔棒/橡胶抽拔管，Φ90*18m，桥梁/预制梁成孔用",
    "must_have": ["Φ90mm", "18m/根", "1800米", "桥梁/预制梁成孔用", "13%专票"],
    "avoid": ["普通密封橡胶棒", "橡胶辊", "抽拔机/拔管机设备"],
    "keywords": {
      "precise": ["橡胶抽拔棒 90*18m", "橡胶抽拔管 90mm 18米"],
      "broad": ["橡胶抽拔棒", "橡胶抽拔管"],
      "scene": ["桥梁成孔橡胶抽拔棒", "预制梁成孔橡胶抽拔管"],
      "supplier": ["橡胶抽拔棒厂家", "衡水橡胶抽拔管厂家"]
    },
    "limit": 20,
    "required_output_fields": ["seller_name", "seller_url", "product_title", "product_url", "price", "location", "match_level"]
  }
}
```

## Keyword Construction Rules

### Dimension variants

Always normalize dimension symbols:

```text
720×400×60
720*400*60
720 400 60
720x400x60
720mm 400mm 60mm
```

### Core formula

```text
核心名称 + 规格
核心名称 + 材质 + 规格
同义词 + 规格
场景 + 核心名称
材质/工艺 + 核心名称
核心名称 + 厂家/批发/定制
```

### Avoid overlong keywords

Do not only produce one huge query. 1688 search works better with multiple shorter queries.

Bad:

```text
13%专票 袖标 9×10CM 印字安全员 带别针 500个 红色布料可开票厂家
```

Good:

```text
安全员袖标 9*10 带别针
安全员臂章 别针款
袖标定制 安全员
```

## Tool Usage

Tool use is optional for common products but recommended for obscure items, model numbers, or industry-specific terms.

| Tool | Use |
|---|---|
| `web_search` | Validate exact model/spec and discover seller wording. |
| `web_extract` | Usually not needed in this skill unless a search result page is needed for keyword discovery. |
| `browser_navigate` | Avoid for this stage unless direct visual confirmation is essential; 1688 often triggers login/captcha. |

### Recommended queries

```text
"完整型号或规格" 商品名
"规格" 商品名 材质
1688 商品名 规格 材质
商品名 同义词 厂家
```

Examples:

```text
"3060YG-24-10K" 子弹头 型号
"720*400*60" 盖板模具 塑料
1688 防撞桶 高1米 玻璃钢
1688 安全员袖标 带别针 9*10 厂家
```

## User-Facing Answer Format

When replying to a human in Chinese, use:

1. **一句话识别**：这是什么，不是什么。
2. **字段解释表**：名称、规格、材质、数量、发票含义。
3. **1688 常见叫法**。
4. **搜索词分组**：精准、放宽、场景、材质/工艺、发票匹配。
5. **询价话术**。
6. **注意事项**。

## Example Output: 袖标

```json
{
  "target_item": "安全员袖标/安全员臂章，小号别针款",
  "item_summary": "用于工地/物业/活动现场的安全员身份标识，9×10cm带别针，通常别在衣服或袖子上。",
  "not_this": ["不是常见14×40cm或15×45cm的大号套臂魔术贴袖章"],
  "parsed_fields": {
    "invoice_type": "13%专票",
    "name": "袖标",
    "spec": "9×10CM,印字安全员，带别针",
    "brand": null,
    "quantity": 500,
    "unit": "个"
  },
  "must_have": ["9×10cm", "印字安全员", "带别针", "500个", "13%专票"],
  "avoid": ["大号套臂款", "不带别针款"],
  "category_terms": ["安全员袖标", "安全员袖章", "安全员臂章", "红袖章"],
  "keywords": {
    "precise": ["安全员袖标 9*10 带别针", "安全员臂章 9*10 别针款", "袖标 安全员 带别针"],
    "broad": ["安全员袖标", "安全员袖章", "安全员臂章", "安全员红袖章"],
    "scene": ["工地安全员袖标", "施工安全员袖章", "安全监督员臂章"],
    "material_or_style": ["织唛安全员臂章", "刺绣安全员臂章", "别针款袖标"],
    "supplier": ["安全员袖标厂家", "袖标定制 安全员", "别针袖标定制"],
    "invoice_matching": ["袖标 9*10cm 安全员 带别针", "安全员袖标 500个 13%专票"]
  },
  "seller_questions": [
    "能否做9×10cm小号别针款？",
    "是否每个都带别针？",
    "材质和工艺是什么？",
    "500个含税单价是多少？",
    "是否可开13%增值税专票？"
  ],
  "pitfalls": ["1688上很多安全员袖章是大号套臂款，询价时必须强调9×10cm、小号、带别针。"]
}
```

## Common Pitfalls

1. **Only using the invoice name.** Always add seller synonyms.
2. **Ignoring material.** Material often changes the product and price.
3. **Ignoring size notation variants.** Search `×`, `*`, spaces, and `x` variants.
4. **Mixing goods and molds.** 模具 is a production tool, not the finished item.
5. **Overloading one query.** Generate multiple short searches instead.
6. **Skipping the invoice requirement.** Include 13% 专票 in seller questions, not necessarily every search query.
7. **Treating site-use rooms as generic housing.** For `保安亭板房 3m*2m*2.8m`, prefer `保安亭/岗亭/门卫室/值班室/成品岗亭/可移动岗亭`; only use `活动板房/集装箱房` as broad fallbacks because they pull in dormitory/office products.

## Verification Checklist

- [ ] Parsed all visible fields.
- [ ] Explained the item in plain Chinese.
- [ ] Flagged common wrong variants.
- [ ] Produced precise, broad, scene, material/style, supplier, and invoice-matching keywords.
- [ ] Included dimension variants when dimensions exist.
- [ ] Included seller questions with 13% 专票 when relevant.
- [ ] Did not collect or rank sellers; hand off to `1688-find-product-links` for that.
- [ ] If a downstream link search is likely, included `link_search_request.required_output_fields` with both `seller_url` and `product_url`.
