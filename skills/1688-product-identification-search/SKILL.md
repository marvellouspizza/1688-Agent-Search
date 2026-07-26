---
name: 1688-product-identification-search
description: Use when the user provides a Chinese invoice/procurement line item and asks what the item is on 1688, how to identify it, and how to compose search keywords. Produces a concise item explanation, search term groups, and seller inquiry prompts.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [1688, procurement, sourcing, chinese-commerce, search-keywords]
    related_skills: [1688-identify-product-keywords, 1688-find-product-links]
---

# 1688 Product Identification and Search Keywords

## Modular Workflow Note

This original skill is now the broad, human-facing overview for “what is this and how should I search on 1688?” workflows.

For agent-to-agent modular sourcing, prefer the two smaller skills:

1. `1688-identify-product-keywords` — identify the product, parse procurement fields, build search keywords, and output structured requirements. Do **not** collect links.
2. `1688-find-product-links` — take the structured keywords/requirements and search for 1688 product links, seller links, prices, regions, and match levels.

Recommended chain:

```text
采购清单/发票行
  -> 1688-identify-product-keywords
  -> structured keywords + must-have requirements
  -> 1688-find-product-links
  -> ranked product/seller link table
```

## Overview

Use this skill when the user is shopping or sourcing on 1688 and provides a Chinese invoice/procurement line item such as:

```text
发票种类 名称 规格型号 品牌 数量 单位
13%专票 防撞桶 高1米左右，玻璃钢 500 个
```

The task is to explain what the thing is, identify likely synonyms/category names used by 1688 sellers, and provide practical search keywords the user can paste into 1688.

The answer should be procurement-oriented: explain the item in plain Chinese, distinguish common lookalikes, and give search terms grouped by precision, synonyms, scene/use case, material/process, and invoice-matching wording.

## When to Use

Use this skill when the user asks any of these:

- “我在 1688 购物，这个东西是什么？”
- “我可以怎样拼搜索词？”
- “这个发票/采购清单里的品名怎么搜？”
- “这个规格型号在 1688 上叫什么？”
- “帮我识别这个采购项，并给搜索关键词。”

Do **not** use this skill for the follow-up task “搜 20 个商家给我” alone; that is a seller-search task. However, this skill’s keyword output should support that later step.

## Input Parsing

First parse the procurement line into fields:

| Field | Meaning | What to infer |
|---|---|---|
| 发票种类 | Tax/invoice requirement | e.g. 13% 专票 means ask for VAT special invoice |
| 名称 | Core item name | Start here, but expect synonyms on 1688 |
| 规格型号 | Dimensions/material/function | Most important for search terms |
| 品牌 | Brand | If blank, treat as generic/unbranded |
| 数量 | Quantity | Helps decide wholesale/custom inquiry wording |
| 单位 | Unit | 个/套/块/米 etc. can clarify item type |

Example:

```text
13%专票 袖标 9×10CM,印字安全员，带别针 500 个
```

Parsed as:

- Item: 袖标
- Size: 9×10cm
- Text: 安全员
- Attachment: 带别针
- Quantity: 500个
- Tax: 13% VAT special invoice
- Likely 1688 name: 安全员袖标 / 安全员臂章 / 小号别针款袖章

## Identification Workflow

### 1. Identify the item’s real category

Do not simply repeat the invoice name. Explain the commercial category:

- “防撞桶” → 道路交通安全设施 / 防撞隔离桶 / 警示桶
- “盖板模具” → 水泥/混凝土预制盖板用塑料模具, not the finished cover plate
- “袖标” → 安全员袖标/袖章/臂章, likely small pin-backed badge if 9×10cm with 别针
- “3060YG-24-10K 子弹头” → 旋挖截齿/钻齿/子弹齿, not ammunition

### 2. Determine whether it is a finished good, consumable, part, or mold

This avoids wrong purchases:

| Procurement wording | Usually means | Warning |
|---|---|---|
| 盖板 | Finished cover plate | Search 成品盖板 |
| 盖板模具 | Mold for casting cover plates | Search 模具, not 盖板成品 |
| 防撞桶 | Finished traffic safety product | Material matters: 玻璃钢 vs PE/滚塑/吹塑 |
| 袖标 | Identification badge/armband | 9×10带别针 is not the large arm-worn sleeve type |
| 子弹头 | Engineering cutter tooth | In construction/drilling context, it is 截齿/旋挖齿 |

### 3. Extract keyword dimensions

Build search keywords from:

1. Core name: 防撞桶 / 盖板模具 / 袖标
2. Synonyms: 袖章、臂章、红袖章; 水泥盖板模具、混凝土盖板模具
3. Material: 玻璃钢、塑料、PP、ABS、涤纶、无纺布
4. Dimensions: 1米、100cm、720*400*60、9*10cm
5. Use scene: 高速、公路、排水沟、电缆沟、工地、安全员
6. Process/style: 印字、带别针、魔术贴、刺绣、织唛、注水、灌砂
7. Purchase terms: 厂家、批发、定制、生产厂家、13%专票

## Search Keyword Construction

Use grouped search terms. Avoid dumping a single long list with no structure.

### Group 1: Precise Search Terms

Use exact name + key specification.

```text
商品名 + 规格
商品名 + 材质 + 规格
商品名 + 规格 + 关键功能
```

Examples:

```text
玻璃钢防撞桶 高1米
塑料盖板模具 720*400*60
安全员袖标 9*10 带别针
```

### Group 2: Synonym / Broad Search Terms

Use seller-friendly names.

```text
防撞桶 厂家
交通防撞桶
水泥盖板模具
混凝土盖板模具
安全员袖章
安全员臂章
```

### Group 3: Scene-Based Search Terms

Use the application scenario.

```text
高速公路防撞桶
市政道路防撞桶
电缆沟盖板模具
排水沟盖板模具
工地安全员袖标
施工安全员袖章
```

### Group 4: Material / Process Search Terms

Use when material or process matters.

```text
玻璃钢防撞桶
滚塑防撞桶
吹塑防撞桶
PP盖板模具
ABS盖板模具
刺绣安全员臂章
织唛安全员臂章
别针款袖标
```

### Group 5: Invoice-Matching Search Terms

Use direct wording that matches the invoice.

```text
防撞桶 高1米 玻璃钢 500个
盖板模具 塑料 720*400*60
袖标 9*10cm 安全员 带别针
```

## Recommended Answer Format

Answer in Chinese using this structure:

1. **一句话识别**：这是什么，不是什么。
2. **字段解释表**：把发票字段翻译成采购含义。
3. **常见叫法**：1688 上卖家可能怎么叫。
4. **搜索词分组**：精准、放宽、场景、材质/工艺、发票匹配。
5. **询价话术**：可复制给卖家。
6. **注意事项/避坑**：材质、尺寸、款式、开票、成品 vs 模具等。

Keep the response practical and concise enough to be pasted into Feishu.

## Example: 防撞桶

Input:

```text
13%专票 防撞桶 高1米左右，玻璃钢 500 个
```

Identification:

- 道路交通安全设施里的防撞桶/防撞隔离桶/警示桶。
- 玻璃钢是材质，区别于普通 PE、滚塑、吹塑塑料防撞桶。

Search terms:

```text
玻璃钢防撞桶 高1米
1米玻璃钢防撞桶
玻璃钢交通防撞桶
防撞桶 1000mm 玻璃钢
道路防撞桶
交通防撞桶
高速公路防撞桶
施工防撞桶
玻璃钢反光防撞桶
防撞桶厂家
```

Seller inquiry:

```text
你好，我要采购防撞桶，数量500个。
要求：高度约1米，材质玻璃钢，带反光膜。
请确认高度、直径、重量、壁厚，是否可注水/灌砂，500个含税单价，是否可开13%专票，运费和交期。
```

Pitfall:

- Do not buy ordinary plastic/PE/滚塑/吹塑桶 if the invoice requires 玻璃钢.

## Example: 盖板模具

Input:

```text
13%专票 盖板模具 塑料 720×400×60mm 500 个
```

Identification:

- 预制混凝土/水泥盖板用塑料模具，不是盖板成品。
- Likely used for 排水沟、电缆沟、电缆槽、铁路/市政工程盖板.

Search terms:

```text
盖板模具 720*400*60
塑料盖板模具 720*400*60
水泥盖板模具 720*400*60
混凝土盖板模具 720*400*60
预制盖板模具 720*400*60
电缆沟盖板模具
排水沟盖板模具
铁路盖板模具
高铁盖板模具
水泥预制盖板塑料模具
```

Seller inquiry:

```text
你好，我要采购塑料盖板模具，数量500个。
规格：720×400×60mm，用于浇筑水泥/混凝土预制盖板。
请确认能否做该规格，尺寸是内径还是外径，材质是PP/ABS还是其他工程塑料，单个重量、壁厚、可重复使用次数，500个含税单价，是否可开13%专票，交期和运费。
```

Pitfall:

- Emphasize 模具. If the user wants the finished concrete cover, search 水泥盖板/混凝土盖板 instead.

## Example: 袖标

Input:

```text
13%专票 袖标 9×10CM,印字安全员，带别针 500 个
```

Identification:

- 安全员袖标/安全员臂章/红袖章，身份标识用品。
- 9×10cm 带别针 usually means small pin-backed badge/arm patch, not the large arm-worn sleeve band.

Search terms:

```text
安全员袖标 9*10 带别针
安全员袖章 9*10cm 别针
安全员臂章 9×10 带别针
袖标 安全员 带别针
红色安全员袖标 别针
安全员袖标
安全员袖章
安全员臂章
安全员红袖章
定制袖标 安全员
别针袖标定制
小袖标定制 9*10
刺绣安全员臂章
织唛安全员臂章
```

Seller inquiry:

```text
你好，我要采购安全员袖标，数量500个。
规格：9×10cm，印字“安全员”，背面/边上需要带别针。
请确认能否做9×10cm小号别针款，材质是布料/织唛/刺绣/无纺布/涤纶哪种，底色和字色，是否每个配别针，500个含税单价，是否可开13%专票，打样时间、交期和运费。
```

Pitfall:

- Many 1688 listings are large sleeve armbands with 魔术贴/松紧带. The invoice says 9×10cm + 带别针, so stress 小号、别针款.

## Tool Usage Pattern

For the initial “what is this and how should I search?” question, tool use is optional but recommended when the item is obscure, model-coded, or industry-specific. In this session the following Hermes tools were used across the workflow:

| Tool | When used | Purpose |
|---|---|---|
| `web_search` | Product identification and keyword validation | Search public web/1688-indexed snippets for exact model names, synonyms, category names, and common seller wording. Examples: `"3060YG-24-10K" 子弹头 型号`, `1688 防撞桶 高1米 玻璃钢`, `"720*400*60" 盖板模具 塑料`, `1688 安全员袖标 带别针 9*10 厂家`. |
| `web_extract` | When a search result points to a 1688 result/category page | Extract summarized product lists, seller names, prices, regions, and category wording from pages such as `安全员袖章`, `水泥盖板模具`, `防撞桶厂家`. It may hit login/captcha; if so, use search snippets or alternative pages. |
| `browser_navigate` | When trying direct interactive access to 1688 pages | Check whether the 1688 page is accessible in browser. In this workflow direct 1688 pages often redirected to Taobao/1688 login or bot detection, so it was not reliable for extraction. |
| `write_file` | When the user requested a Feishu-document-ready method summary | Create a Markdown document file summarizing the method so the user could paste/upload it to Feishu. |
| `skill_view` | When creating/updating this skill | Load skill-authoring guidance and inspect the current skill content before patching. |
| `skill_manage` | When the user requested saving the workflow as a skill | Create this reusable skill and patch it with the tool-usage section. |

### Recommended Tool Sequence

1. **Start with `web_search` for grounding** when the product name/model may be ambiguous.
   - Search exact model/size first.
   - Then search broader category terms.
   - Add `1688`, `厂家`, `批发`, or `site:1688.com` when useful.

2. **Use `web_extract` on 1688 category/search pages** when available.
   - Good for getting seller wording, common product titles, price bands, and supplier names.
   - If extraction returns login/captcha, do not rely on it; switch to other search results and snippets.

3. **Use `browser_navigate` only if interaction is needed.**
   - Direct 1688 access often triggers login/captcha.
   - Do not spend too long trying to bypass; summarize the limitation and proceed with indexed search data.

4. **Use `write_file` for long reusable docs.**
   - If the user asks for a Feishu document, produce Markdown with headings/tables/code blocks.
   - Return the file path as `MEDIA:/absolute/path.md` when appropriate.

5. **Use `skill_manage` after a reusable workflow emerges.**
   - Create/update a skill when the user asks to save the method or the workflow is likely to recur.

### Tool Query Templates

Use these web search templates:

```text
"完整型号或规格" 商品名
"规格" 商品名 材质
1688 商品名 规格 材质
site:1688.com 商品名 规格 厂家
商品名 厂家 1688
商品同义词 批发 1688
```

Examples:

```text
"3060YG-24-10K" 子弹头 型号
3060YG-24-10K 截齿
1688 防撞桶 高1米 玻璃钢
"720*400*60" 盖板模具 塑料
1688 水泥盖板模具 塑料 厂家
1688 安全员袖标 带别针 9*10 厂家
```

## Common Pitfalls

1. **Searching only the invoice name.** 1688 sellers may use synonyms: 袖标→袖章/臂章, 盖板模具→水泥盖板模具/预制盖板模具.

2. **Ignoring material.** 玻璃钢, PE, PP, ABS, 滚塑, 吹塑, 织唛, 刺绣 can describe very different products and prices.

3. **Mixing finished goods with molds.** “盖板” and “盖板模具” are different purchases.

4. **Not converting dimension symbols.** Search all variants: `720×400×60`, `720*400*60`, `720 400 60`, `720x400x60`.

5. **Not asking whether dimensions are inner or outer dimensions.** For molds, this is crucial.

6. **Not confirming invoice wording.** Ask if the seller can issue 13% VAT special invoice and whether the invoice item name can match the procurement line.

7. **Failing to mention quantity.** 500 units changes price, packaging, shipping, and whether custom production is worthwhile.

## Verification Checklist

Before finalizing the response:

- [ ] Explain what the item is in plain Chinese.
- [ ] State what it is **not** if there is a common confusion.
- [ ] Parse the key fields: name, material, size, quantity, invoice.
- [ ] Provide at least 4 groups of search terms.
- [ ] Include exact dimension variants if dimensions are present.
- [ ] Include synonyms commonly used by 1688 sellers.
- [ ] Include a copy-paste seller inquiry template.
- [ ] Mention 13% 专票 if the input has it.
- [ ] Add at least one practical pitfall/warning.
