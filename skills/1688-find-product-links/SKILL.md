---
name: 1688-find-product-links
description: Use when an agent already has identified 1688 product keywords and needs to find product links, seller names, store links, product links, prices, regions, match levels, and limitations from 1688 or indexed search results. Must preserve both seller_url and product_url when available.
version: 1.0.3
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [1688, sourcing, seller-search, product-links, web-search]
    related_skills: [1688-identify-product-keywords, 1688-product-identification-search]
---

# 1688 Find Product and Seller Links

## Overview

Use this skill for the second stage of 1688 sourcing: taking already-identified product keywords and finding candidate 1688 product/seller links.

This skill should produce a ranked table of products or merchants, with match reasons and missing information to verify with sellers.

It assumes the first-stage skill `1688-identify-product-keywords` has already determined:

- What the item is
- Required specs/materials/quantity
- Search keywords and synonyms
- Wrong variants to avoid

## When to Use

Use when the user or another agent asks:

- “帮我在 1688 找 20 个商家。”
- “用这些关键词找商品链接。”
- “找能卖这个规格的 1688 店铺。”
- “给我商家名、链接、价格、地区、匹配度。”

Do not use this skill for initial product recognition. If the item is not identified yet, first use `1688-identify-product-keywords`.

## Input Contract

Prefer structured input from the identification skill.

```json
{
  "target_item": "安全员袖标，小号别针款",
  "must_have": ["9×10cm", "印字安全员", "带别针", "500个", "13%专票"],
  "avoid": ["大号套臂魔术贴袖章"],
  "keywords": {
    "precise": ["安全员袖标 9*10 带别针", "安全员臂章 9*10 别针款"],
    "broad": ["安全员袖标", "安全员袖章", "安全员臂章"],
    "supplier": ["安全员袖标厂家", "袖标定制 安全员"]
  },
  "limit": 20
}
```

If input is not structured, ask for or infer:

- Target item
- Must-have specs
- Keywords
- Desired result count

## Output Contract

Return a concise summary and a ranked table. For agent-to-agent use, JSON is preferred.

### JSON schema

```json
{
  "search_summary": "string",
  "queries_used": ["string"],
  "results": [
    {
      "rank": 1,
      "seller_name": "string|null",
      "seller_url": "string|null: 1688 shop/store/company URL; do not put product URL here",
      "product_title": "string",
      "product_url": "string|null: direct detail.1688.com offer URL; do not put shop URL here",
      "source_url": "string|null: search/category page where this row was found",
      "price": "string|null",
      "sales_or_transactions": "string|null",
      "location": "string|null",
      "years_on_1688": "string|null",
      "match_level": "很高|高|中|低",
      "matched_requirements": ["string"],
      "missing_or_unclear": ["string"],
      "why_match": "string",
      "recommended_question": "string"
    }
  ],
  "limitations": ["string"],
  "next_steps": ["string"]
}
```

### Human-facing table format

| 序号 | 商家/公司 | 商家链接 | 商品标题 | 商品链接 | 价格 | 地区 | 匹配度 | 备注 |
|---:|---|---|---|---|---|---|---|---|

Never merge `商家链接` and `商品链接` into a single `链接` column when both are available. If only one link is available, put `待确认` in the missing column.

Always include a limitations note if links could not be directly verified because of 1688 login/captcha.

## Tool Usage Pattern

This skill is tool-driven. Use tools until enough candidates are found or limitations are clear.

| Tool | Required? | Use |
|---|---:|---|
| `web_search` | Yes | Search indexed 1688 pages, product titles, seller pages, and company pages. |
| `web_extract` | Recommended | Extract 1688 category/search pages or product pages when accessible. |
| `browser_navigate` | Optional | Directly open pages only if needed; 1688 often triggers login/captcha. |
| `browser_console` | Optional | Extract result-card DOM from accessible 1688 aggregation pages; classify `detail.1688.com/offer/` as `product_url` and `*.1688.com/` shop domains as `seller_url`. |
| `terminal` | Optional | Run `lark-cli`/`feishu-cli`, zip screenshot folders, or use headless Chrome for screenshots when requested. |
| `vision_analyze` / `browser_vision` | Optional | Verify whether a screenshot is an actual product/search page or just 1688 login/captcha. |
| `write_file` | Optional | Save long result tables as Markdown/CSV if requested. |

Current known environment pattern from prior work:

- `web_search` and `web_extract` may use Hermes `web` backend such as Firecrawl, depending on config.
- Direct 1688 pages frequently redirect to Taobao/1688 login or bot detection.
- If `web_extract` returns captcha/login content, do not claim page verification; rely on indexed snippets and accessible category summaries.

## Search Strategy

For Feishu document/Base delivery, screenshot evidence, and the lark-cli workflow, see `references/1688-feishu-screenshot-reporting.md`. Use it when the user asks for 商家链接 + 商品链接 + 商品页截图 + 飞书文档 or 飞书多维表格.

### 1. Start with precise keywords

Use exact must-have terms:

```text
site:1688.com 商品名 规格 材质
商品名 规格 材质 1688
"规格" 商品名 1688
```

Examples:

```text
site:1688.com 安全员袖标 带别针
1688 安全员袖标 带别针 9*10 厂家
"720*400*60" 盖板模具 塑料
site:1688.com 玻璃钢防撞桶 高1米 厂家
```

### 2. Search 1688 category pages

Search broader 1688 category pages:

```text
1688 商品名 厂家
1688 商品名 批发
1688 商品同义词 厂家
site:s.1688.com 商品名 厂家
site:taizhou.1688.com/shop/m/gongsi_search 商品名 厂家
```

### 3. Search by synonym and scene

If precise queries are sparse, use broad and scene terms from `1688-identify-product-keywords`.

Examples:

```text
水泥盖板模具厂家 1688
排水沟盖板模具 1688
安全员臂章 别针款 1688
玻璃钢防撞桶 生产厂家 1688
```

### 4. Extract accessible pages

Use `web_extract` on promising 1688 result/category pages. Extract:

- Product titles
- Seller names
- Seller/shop/store URLs (`seller_url`)
- Product/detail/offer URLs (`product_url`)
- Prices
- Transaction counts
- Store years
- Regions

### 4a. Preserve both link types

When extracting from 1688 result pages, each result often contains two distinct anchors:

- Product/offer link: usually `https://detail.1688.com/offer/<id>.html`
- Seller/store link: usually `https://<shop>.1688.com/` or a 1688 company/shop URL

Always capture both separately:

```json
{
  "seller_name": "衡水乐恒橡胶制品有限公司",
  "seller_url": "https://hslhxj.1688.com/",
  "product_title": "橡胶抽拔棒 工程用橡胶抽拔管40 45 70 80 90 100mm圆形成孔黑色",
  "product_url": "https://detail.1688.com/offer/611317010559.html"
}
```

Do **not** output only one generic `url`. If tool extraction gives only snippets without links, use `null`/`待确认` and explain the limitation.

### 5. Deduplicate

Deduplicate by seller name and by product URL. If one seller appears multiple times, keep the best-matching product and note repeated presence if useful.

## Result Scoring

Rank results by match level.

### 很高

Use when title or extracted content explicitly matches core item and at least one must-have spec/style.

Examples:

- `玻璃钢防撞桶生产厂家` for glass fiber reinforced plastic anti-collision barrel
- `安全员系列臂章 ... 别针款臂章` for pin-backed safety officer badge
- `塑料盖板模具 720*400*60` for exact mold dimensions

### 高

Matches core item and category, but one must-have spec needs seller confirmation.

### 中

Same broad category, but material/size/style may differ.

### 低

Adjacent product only; include only if results are scarce and clearly mark as backup.

## Required Fields to Capture

Try to capture:

- `seller_name`
- `seller_url` — shop/store/company link. Prefer a `*.1688.com/` store URL when visible.
- `product_title`
- `product_url` — direct product/offer link. Prefer `https://detail.1688.com/offer/<id>.html` when visible.
- `source_url` — search/category/result page where the candidate was found, useful for auditing.
- `price`
- `sales_or_transactions`
- `location`
- `years_on_1688`
- `match_level`
- `why_match`
- `missing_or_unclear`

If a field is unavailable, use `null` or `待确认`, not invented values.

### Link Field Rules

Use explicit URL fields:

| Field | Meaning | Examples | Do not use for |
|---|---|---|---|
| `seller_url` | 1688 shop/store/company URL | `https://hslhxj.1688.com/`, `https://shop32606n8612720.1688.com/` | Direct product offer pages |
| `product_url` | 1688 product/detail/offer URL | `https://detail.1688.com/offer/611317010559.html` | Store homepage |
| `source_url` | Search/category/page URL where result was found | `https://tw.1688.com/item/...` | Replacing seller/product URLs |

When using browser DOM extraction, inspect each result row's anchors and map them by URL pattern:

```javascript
Array.from(document.querySelectorAll('li')).map(li => ({
  text: li.innerText,
  links: Array.from(li.querySelectorAll('a')).map(a => ({text: a.innerText, href: a.href}))
}))
```

Then classify:

- `href.includes('detail.1688.com/offer/')` → `product_url`
- `href.match(/^https:\/\/[^/]+\.1688\.com\/?/)` and not `detail.1688.com` → `seller_url`

If one URL appears in both places due to extraction noise, leave the uncertain field `null` and note `missing_or_unclear`.

## Handling 1688 Login/Captcha

1688 often blocks direct extraction. Follow these rules:

1. Do not try to bypass captcha.
2. If browser navigation lands on login, stop using browser for that URL.
3. If `web_extract` returns captcha/login text, mark extraction as unavailable.
4. Use search snippets, accessible category summaries, and multiple queries for corroboration.
5. Clearly state limitations:

```text
部分 1688 页面触发登录/验证码，以下结果基于搜索摘要和可抽取页面整理，具体规格、库存和开票需向商家确认。
```

### Product-page screenshot requests

If the user asks for product-page screenshots, try to capture them but verify the image content before claiming success. In prior 1688 runs, direct `detail.1688.com/offer/...` screenshots commonly rendered the 1688 login page rather than product details.

Recommended fallback:

1. Capture an accessible search/category/aggregation page screenshot (for example a `tw.1688.com/item/...` page showing many result cards).
2. Capture attempted product URL screenshots only as evidence of login interception, not as product-page proof.
3. If many screenshots are requested, make a contact sheet plus a zip of raw screenshots.
4. State the limitation in the report: product detail pages were blocked by 1688 login/captcha, while candidate rows came from accessible search/aggregation pages or indexed extraction.

## Query Templates

```text
site:1688.com 商品名 规格 材质
site:detail.1688.com/offer 商品名 规格
site:s.1688.com 商品名 厂家
site:taizhou.1688.com/shop/m/gongsi_search 商品名 厂家
"精确规格" 商品名
"产品标题中的关键短语"
商品名 生产厂家 1688
商品名 批发 1688
商家名 商品名
```

## Example Workflow: 袖标

Input keywords:

```json
{
  "target_item": "安全员袖标，小号别针款",
  "must_have": ["9×10cm", "印字安全员", "带别针", "500个", "13%专票"],
  "keywords": {
    "precise": ["安全员袖标 9*10 带别针", "安全员臂章 9*10 别针款"],
    "broad": ["安全员袖标", "安全员臂章"],
    "supplier": ["安全员袖标厂家"]
  }
}
```

Search queries:

```text
1688 安全员袖标 带别针 9*10 厂家
site:1688.com 安全员臂章 别针款
安全员系列臂章 别针款 1688
安全员袖章批发 阿里巴巴
```

Candidate result row:

```json
{
  "rank": 1,
  "seller_name": "龙港市踏云科技有限公司",
  "seller_url": "https://example-shop.1688.com/",
  "product_title": "安全员系列臂章监督织唛监察袖标员工臂章班长组长质检别针款臂章",
  "product_url": "https://detail.1688.com/offer/973960758521.html",
  "source_url": "https://s.1688.com/...",
  "price": "¥1.60",
  "location": "温州市",
  "match_level": "很高",
  "matched_requirements": ["安全员", "臂章", "别针款"],
  "missing_or_unclear": ["9×10cm需询问", "13%专票需确认"],
  "why_match": "标题明确包含安全员系列臂章和别针款。",
  "recommended_question": "请确认能否做9×10cm、印字安全员、每个带别针，并可开13%专票。"
}
```

## Common Pitfalls

1. **Claiming verification when only snippets were available.** Be explicit about source limitations.
2. **Collapsing seller and product links into one field.** Always preserve `seller_url` and `product_url` separately when available.
3. **Mixing product and seller pages.** A category page may list products without stable direct links; mark URL type if unclear.
4. **Over-ranking broad sellers.** A general supplier is not a high match unless the title/category matches the target item.
5. **Ignoring missing specs.** If size/material/invoice ability is not visible, put it under `missing_or_unclear`.
6. **Returning duplicate sellers.** Deduplicate unless the user explicitly wants product-level entries.
7. **Not using the identification output.** Must-have and avoid terms should drive ranking.
8. **Claiming screenshots show product pages without checking.** Direct 1688 offer screenshots may be login pages. Verify screenshots visually; if blocked, present them as login-interception evidence and include an accessible search-results screenshot instead.
9. **Using absolute `@/tmp/file.md` with `lark-cli docs +create`.** `lark-cli` requires `--markdown @file` paths to be relative to the current working directory. `cd /tmp` and use `--markdown '@report.md'`.
10. **Broad `docs +update` after inserting media.** Some lark-cli update operations can rewrite unsupported media/file blocks. Re-insert attachments and verify with `docs +fetch` after broad updates.
11. **Using attachment field names with `lark-cli base +record-upload-attachment`.** In observed runs, `--field-id '截图/附件'` could return HTTP 404, while the actual field ID (e.g. `fldEwo98oR`) worked. Run `base +field-list` and use the field ID for attachment uploads.
12. **Assuming `tw.1688.com/pic/...` has seller links.** Image result pages may expose many `detail.1688.com/offer/...` product links and image/title data but no shop link. Keep `seller_url` as `null`/`待确认` unless a store/factory URL is corroborated via search or extract. If separate seller links are required, try `tw.1688.com/item/...` pages and parse compact GBK result-card HTML; those pages often include both `detail.1688.com/offer/...` and `//shopxxxx.1688.com` anchors.
13. **False-negative Feishu media verification.** `lark-cli docs +fetch` may only return the first chunk of a long document, so inserted images/files near the end may not appear in the default fetch output. Fetch a tail offset (e.g. `--offset 13500 --limit 3000`) before deciding media insertion failed.

## Verification Checklist

- [ ] Input includes target item and keywords; if not, infer or ask.
- [ ] Used several keyword variants, not only one query.
- [ ] Tried `web_extract` for promising 1688 pages when accessible.
- [ ] Deduplicated sellers/products.
- [ ] Did not invent unavailable prices, years, locations, or URLs.
- [ ] Captured `seller_url` and `product_url` as separate fields whenever available.
- [ ] Did not put a product offer URL in `seller_url`, or a shop URL in `product_url`.
- [ ] Each result has match level and match reason.
- [ ] Missing specs are called out.
- [ ] Login/captcha limitations are stated when relevant.
- [ ] If screenshots were requested, verified whether each screenshot is an actual product page or a login/captcha page; did not mislabel login screenshots as product screenshots.
- [ ] If a Feishu document was requested, created it with `lark-cli docs +create` using a relative `@file`, inserted media/attachments, and verified with `lark-cli docs +fetch`.
- [ ] If a Feishu Base/多维表格 was requested, created/renamed a table, inserted records with separate `seller_url`/`product_url`, uploaded screenshots to an attachment field using the actual `fld...` ID, and verified row count with `base +record-list`.
- [ ] Output count matches requested limit as closely as possible.
