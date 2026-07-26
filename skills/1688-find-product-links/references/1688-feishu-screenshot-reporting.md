# 1688 Feishu Report + Screenshot Workflow

Session learning from a 1688 sourcing task for `保安亭板房 3m*2m*2.8m`.

## Reliable 1688 extraction pattern

Direct `detail.1688.com/offer/...` pages often redirect to the 1688 login page in headless/local browser contexts. For sourcing tables, a more reliable path was:

1. Search public/indexed pages with `web_search`.
2. Open an accessible `tw.1688.com/item/...` or `tw.1688.com/pic/...` search/category aggregation page with `browser_navigate` or direct HTTP.
3. Use `browser_console` to inspect result cards when the DOM is visible, or use Python `requests` + `BeautifulSoup` for `tw.1688.com/pic/...` pages.
4. Extract and classify anchors:
   - `detail.1688.com/offer/<id>.html` -> `product_url`
   - `https://<shop>.1688.com/` -> `seller_url`
5. Save both link types separately; do not collapse them into a generic `url`.

For `tw.1688.com/pic/...` pages, HTTP often returns GBK HTML containing product links, image URLs, and titles even when browser console sees an empty anchor list. Use:

```python
import requests
from bs4 import BeautifulSoup

url = 'https://tw.1688.com/pic/...html'
r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
r.encoding = 'gbk'
soup = BeautifulSoup(r.text, 'html.parser')
for a in soup.find_all('a'):
    href = a.get('href') or ''
    if 'detail.1688.com/offer/' in href:
        img = a.find('img')
        print({
            'product_url': href,
            'title': a.get('title') or (img and img.get('alt')),
            'image_url': img and img.get('src'),
        })
```

Caveat: `tw.1688.com/pic/...` image pages may not expose seller/shop links. Keep `seller_url` as `null`/`待确认` unless a store/factory URL is found elsewhere.

For `tw.1688.com/item/...` result pages, HTTP often returns GBK HTML with enough card structure to extract **both** product and seller links. This worked for a 20-row `盖板模具 塑料 720×400×60mm` sourcing report where direct `s.1688.com` and `detail.1688.com` were login-gated:

```python
import re, requests
from bs4 import BeautifulSoup

url = 'http://tw.1688.com/item/-CBAEC4E0B9B5B8C7B0E5C4A3BEDF.html?beginPage=4'
r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=25)
r.encoding = 'gbk'
soup = BeautifulSoup(r.text, 'html.parser')
seen = set()
for a in soup.find_all('a'):
    href = a.get('href') or ''
    if href.startswith('//'):
        href = 'https:' + href
    m = re.search(r'detail\.1688\.com/offer/(\d+)', href)
    if not m or m.group(1) in seen:
        continue
    # climb to the compact product card containing price/title/seller/location
    card = a
    for _ in range(8):
        txt = card.get_text(' ', strip=True) if card else ''
        if '¥' in txt and ('年' in txt or '月均' in txt or '回頭率' in txt) and len(txt) < 450:
            break
        card = card.parent if card else None
    if not card:
        continue
    links = [(x.get_text(' ', strip=True), x.get('href') or '') for x in card.find_all('a')]
    seller = next(((t, h) for t, h in links
                   if 'detail.1688.com' not in h and h.strip('/').endswith('.1688.com') and t), None)
    title = max([t for t, h in links if 'detail.1688.com/offer/' in h and t and '¥' not in t], key=len)
    print({'product_url': href, 'seller_name': seller and seller[0], 'seller_url': seller and ('https:' + seller[1] if seller[1].startswith('//') else seller[1]), 'title': title})
    seen.add(m.group(1))
```

Prefer `tw.1688.com/item/...` over `tw.1688.com/pic/...` when the user requires separate `seller_url`, because item pages are more likely to include shop anchors like `//shopxxxx.1688.com` or `//brand.1688.com` in each card.

Example DOM extraction shape:

```javascript
(() => {
  const rows = [];
  const nodes = Array.from(document.querySelectorAll('li, div')).filter(el => {
    const t = el.innerText || '';
    return t.includes('保安亭') && t.length > 20 && t.length < 1000;
  });
  for (const el of nodes) {
    const links = Array.from(el.querySelectorAll('a')).map(a => ({text: a.innerText, href: a.href}));
    const product = links.find(l => l.href.includes('detail.1688.com/offer/'));
    const seller = links.find(l => /^https:\/\/[^/]+\.1688\.com\/?/.test(l.href) && !l.href.includes('detail.1688.com'));
    if (product) rows.push({text: el.innerText, links, product_url: product.href, seller_url: seller?.href || null});
  }
  return rows;
})()
```

## Screenshot policy

When users ask for “商品页截图”:

- Try direct screenshots of `product_url` pages.
- Verify whether screenshots show actual product detail content or only a login/captcha page, using vision analysis if available.
- If all product pages are blocked by login, state that limitation clearly and provide:
  1. a screenshot of the accessible search/category result page;
  2. a contact sheet of attempted product pages showing the login interception;
  3. a zip of raw screenshots if the user requested evidence.

Headless Chrome screenshot command on macOS:

```bash
'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  --headless=new --disable-gpu --no-sandbox --window-size=1440,2400 \
  --screenshot=/tmp/search_results.png 'https://tw.1688.com/item/...'
```

## Feishu/lark-cli Base (多维表格) workflow

Use this when the user asks to organize 1688 sourcing results into a Feishu Base / 多维表格 rather than a document.

### 1. Create the Base and inspect defaults

```bash
lark-cli base +base-create \
  --name '1688采购调研：商品名/规格' \
  --time-zone Asia/Shanghai

lark-cli base +table-list --base-token '<base_token>'
lark-cli base +field-list --base-token '<base_token>' --table-id '<tbl...>'
```

New bases may include a default empty table. Prefer reusing and renaming it, then add fields, rather than creating duplicate blank tables.

### 2. Table shape for sourcing results

Recommended fields for the main table:

- `序号`
- `商家/公司`
- `商家链接`
- `商品标题`
- `商品链接`
- `来源页`
- `价格`
- `成交`
- `年限`
- `地区`
- `匹配度`
- `备注`
- `商品图URL`
- `截图/附件` as attachment field

Also create a small second table such as `识别与搜索词` with fields `项目` and `内容` for the product interpretation, search keywords, inquiry checklist, screenshot limitations, and data sources.

### 3. Create records from JSON

`lark-cli base +record-batch-create --json @file` has the same relative-path quirk as `docs +create`: run from the directory containing the file.

```bash
cd /tmp
lark-cli base +record-batch-create \
  --base-token '<base_token>' \
  --table-id '<tbl...>' \
  --json @records.json
```

A compact records JSON shape that worked:

```json
{
  "fields": ["序号", "商家/公司", "商家链接", "商品标题", "商品链接", "来源页", "价格", "成交", "年限", "地区", "匹配度", "备注", "商品图URL"],
  "rows": [["1", "供应商名", "https://shop.1688.com/", "商品标题", "https://detail.1688.com/offer/123.html", "https://s.1688.com/...", "¥100", "成交1笔", "5年", "地区", "很高", "备注", "https://cbu01.alicdn.com/..."]]
}
```

### 4. Upload per-record screenshot attachments

Upload attempted product-page screenshots into the attachment field after creating rows. Important: `--field-id '截图/附件'` may return HTTP 404 even though `field-list` shows that name. Use the actual `fld...` field ID.

```bash
lark-cli base +field-list --base-token '<base_token>' --table-id '<tbl...>'

cd /tmp/screens
lark-cli base +record-upload-attachment \
  --base-token '<base_token>' \
  --table-id '<tbl...>' \
  --record-id '<rec...>' \
  --field-id '<fld_attachment_id>' \
  --file './01_offer_123.png' \
  --name '01_offer_123.png'
```

If `detail.1688.com` screenshots are login pages, label them as login-interception evidence in the summary table, not as successful product-page screenshots.

### 5. Verify Base output

```bash
lark-cli base +base-get --base-token '<base_token>'
lark-cli base +record-list --base-token '<base_token>' --table-id '<tbl...>' --limit 25 > /tmp/record_list.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/record_list.json'))
print('ok', d.get('ok'), 'records', len(d['data']['record_id_list']))
print('rows_with_attachment_like', sum(any(isinstance(x, list) for x in row) for row in d['data']['data']))
PY
```

Expected for a 20-row sourcing table with screenshots: `records 20` and attachment-like rows near 20.

## Feishu/lark-cli document workflow

Use `lark-cli docs +create` for a Markdown report. Important CLI quirk: `--markdown @file` requires a **relative path within the current directory**; `@/tmp/file.md` fails validation.

Working pattern:

```bash
cd /tmp
lark-cli docs +create \
  --title '1688采购调研：商品名/规格' \
  --markdown '@report.md'
```

Insert images and screenshot archives after creating the doc:

```bash
lark-cli docs +media-insert --doc '<doc-url-or-token>' \
  --file './search_results.png' --type image --caption '1688搜索结果页截图' --align center

lark-cli docs +media-insert --doc '<doc-url-or-token>' \
  --file './screenshots.zip' --type file --caption '原始截图包'
```

If later doing `docs +update`, be careful: replace operations may rewrite unsupported media/file HTML blocks and can temporarily drop file attachment blocks. Re-insert file attachments after broad document updates and verify with `lark-cli docs +fetch`.

When verifying media inserted near the end of a long document, `docs +fetch` may return only the first chunk by default. Use an offset near the end before concluding media is missing:

```bash
lark-cli docs +fetch --doc '<doc-url-or-token>' --offset 13500 --limit 3000
```

Look for escaped or raw media markers such as `<image token="..." .../>`, `<view ...><file token="..." name="screenshots.zip"/></view>`. If the first fetch chunk lacks image/file tokens but the media-insert commands returned `"ok": true`, fetch the tail chunk before reinserting.

For screenshot deliverables, a useful pattern is: create a screenshot of the accessible `tw.1688.com/item/...` result page, capture attempted `detail.1688.com/offer/...` pages, generate a contact sheet, then upload both the contact sheet and a zip. Verify the contact sheet with vision: in 1688 runs it often shows one real search-results page plus many login/QR pages, which should be described as login-interception evidence rather than product-page screenshots.

## Verification checklist

- `lark-cli docs +fetch --doc <url>` returns `"ok": true`.
- For Base deliverables, `lark-cli base +base-get --base-token <base_token>` returns `"ok": true` and `record-list` shows the expected row count.
- Markdown contains the title and the 20-row table.
- Fetch output contains `<image token=...>` for inserted screenshots.
- Fetch output contains `<file token=... name="...zip"/>` if screenshot zip was inserted.
- Local report, screenshots, and zip are non-empty before final reply.
