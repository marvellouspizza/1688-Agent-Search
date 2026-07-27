import { lookup } from "node:dns/promises";
import { BlockList, isIP } from "node:net";

import type { PurchaseConfig } from "../../config.js";
import type { ToolEntry } from "../registry.js";
import { readBoundedResponse } from "./searxng.js";

const BLOCKED_ADDRESSES = buildBlockedAddresses();

export const WEB_EXTRACT_SCHEMA = {
  type: "object",
  properties: {
    url: { type: "string", minLength: 8, maxLength: 2_000 },
    max_characters: { type: "integer", minimum: 500, maximum: 30_000 },
  },
  required: ["url"],
  additionalProperties: false,
} as const;

export async function validatePublicUrl(value: string): Promise<string> {
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch (error) {
    throw new Error("url 必须是公开 HTTP(S) 地址", { cause: error });
  }
  if (!(url.protocol === "http:" || url.protocol === "https:") || !url.hostname || url.username || url.password) {
    throw new Error("url 必须是公开 HTTP(S) 地址");
  }
  if (url.hostname.toLowerCase() === "localhost") throw new Error("禁止访问本机地址");
  let addresses: readonly { address: string; family: number }[];
  if (isIP(url.hostname)) {
    addresses = [{ address: url.hostname, family: isIP(url.hostname) }];
  } else {
    try {
      addresses = await lookup(url.hostname, { all: true, verbatim: true });
    } catch (error) {
      throw new Error("无法解析目标域名", { cause: error });
    }
  }
  for (const { address, family } of addresses) {
    if (BLOCKED_ADDRESSES.check(address, family === 6 ? "ipv6" : "ipv4")) {
      throw new Error("禁止访问私有或保留网络地址");
    }
  }
  return url.toString();
}

export async function webExtractHandler(
  arguments_: Record<string, unknown>,
  config: PurchaseConfig,
  fetchImpl: typeof fetch = fetch,
): Promise<Record<string, unknown>> {
  const unknown = Object.keys(arguments_).filter((key) => key !== "url" && key !== "max_characters");
  const maximum = arguments_.max_characters ?? 12_000;
  if (unknown.length > 0 || typeof arguments_.url !== "string" || typeof maximum !== "number"
    || !Number.isInteger(maximum) || maximum < 500 || maximum > 30_000) {
    throw new Error("web_extract 参数格式无效");
  }
  let currentUrl = await validatePublicUrl(arguments_.url);
  let response: Response | undefined;
  for (let redirect = 0; redirect <= 5; redirect += 1) {
    try {
      response = await fetchImpl(currentUrl, {
        headers: { "User-Agent": "as1688/1.0.0", Accept: "text/html,text/plain" },
        redirect: "manual",
        signal: AbortSignal.timeout(config.requestTimeoutSeconds * 1_000),
      });
    } catch (error) {
      throw new Error(`网页提取失败：${error instanceof Error ? error.message : String(error)}`, { cause: error });
    }
    if (![301, 302, 303, 307, 308].includes(response.status)) break;
    const location = response.headers.get("location");
    if (!location || redirect === 5) throw new Error("网页重定向无效或次数过多");
    currentUrl = await validatePublicUrl(new URL(location, currentUrl).toString());
  }
  if (!response) throw new Error("网页提取失败：没有响应");
  if (!response.ok) throw new Error(`网页提取失败（HTTP ${response.status}）`);
  const raw = await readBoundedResponse(response, 2_000_000, "网页响应过大");
  let decoded = new TextDecoder("utf-8", { fatal: false }).decode(raw);
  const contentType = response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
  if (contentType === "text/html") decoded = htmlToText(decoded);
  return { url: currentUrl, content: decoded.slice(0, maximum), truncated: decoded.length > maximum };
}

export function buildWebExtractEntry(config: PurchaseConfig): ToolEntry {
  return {
    name: "web_extract",
    description: "提取公开网页的受限文本；不能访问内网、本机或证明登录后信息。",
    inputSchema: WEB_EXTRACT_SCHEMA,
    handler: (arguments_) => webExtractHandler(arguments_, config),
    parallelSafe: true,
  };
}

function htmlToText(html: string): string {
  return html
    .replace(/<(script|style|noscript|svg)\b[^>]*>[\s\S]*?<\/\1\s*>/giu, " ")
    .replace(/<!--([\s\S]*?)-->/gu, " ")
    .replace(/<[^>]+>/gu, "\n")
    .replace(/&nbsp;/giu, " ")
    .replace(/&amp;/giu, "&")
    .replace(/&lt;/giu, "<")
    .replace(/&gt;/giu, ">")
    .replace(/&quot;/giu, "\"")
    .replace(/&#39;|&apos;/giu, "'")
    .split(/\r?\n/u)
    .map((part) => part.replace(/\s+/gu, " ").trim())
    .filter(Boolean)
    .join("\n");
}

function buildBlockedAddresses(): BlockList {
  const list = new BlockList();
  for (const [address, prefix] of [
    ["0.0.0.0", 8], ["10.0.0.0", 8], ["100.64.0.0", 10], ["127.0.0.0", 8],
    ["169.254.0.0", 16], ["172.16.0.0", 12], ["192.0.0.0", 24], ["192.0.2.0", 24],
    ["192.168.0.0", 16], ["198.18.0.0", 15], ["198.51.100.0", 24], ["203.0.113.0", 24],
    ["224.0.0.0", 4], ["240.0.0.0", 4],
  ] as const) list.addSubnet(address, prefix, "ipv4");
  for (const [address, prefix] of [
    ["::", 128], ["::1", 128], ["fc00::", 7], ["fe80::", 10], ["ff00::", 8],
    ["2001:db8::", 32], ["::ffff:0:0", 96],
  ] as const) list.addSubnet(address, prefix, "ipv6");
  return list;
}
