import { performance } from "node:perf_hooks";

export class SearxngError extends Error {
  override readonly name = "SearxngError";
}

export interface SearxngResult {
  readonly title: string;
  readonly url: string;
  readonly snippet: string;
  readonly engine: string;
}

export function validateSearxngBaseUrl(value: string): string {
  const normalized = value.trim().replace(/\/+$/u, "");
  let url: URL;
  try {
    url = new URL(normalized);
  } catch (error) {
    throw new Error("searxng_base_url 必须是完整的 HTTP(S) 地址", { cause: error });
  }
  if (!(["http:", "https:"] as string[]).includes(url.protocol) || !url.host) {
    throw new Error("searxng_base_url 必须是完整的 HTTP(S) 地址");
  }
  if (url.pathname !== "/" || url.search || url.hash) {
    throw new Error("searxng_base_url 不能包含路径、查询参数或片段");
  }
  return normalized;
}

export async function searchSearxng(options: {
  readonly baseUrl: string;
  readonly query: string;
  readonly limit: number;
  readonly timeoutSeconds: number;
  readonly fetchImpl?: typeof fetch;
}): Promise<Record<string, unknown>> {
  const baseUrl = validateSearxngBaseUrl(options.baseUrl);
  const query = options.query.trim();
  if (query.length < 2 || query.length > 300) throw new Error("query 长度必须在 2 到 300 个字符之间");
  if (!Number.isInteger(options.limit) || options.limit < 1 || options.limit > 20) throw new Error("limit 必须在 1 到 20 之间");
  const url = new URL(`${baseUrl}/search`);
  url.search = new URLSearchParams({ q: query, format: "json" }).toString();
  const started = performance.now();
  let response: Response;
  try {
    response = await (options.fetchImpl ?? fetch)(url, {
      headers: { Accept: "application/json", "User-Agent": "as1688/1.0.0" },
      signal: AbortSignal.timeout(options.timeoutSeconds * 1_000),
    });
  } catch (error) {
    throw new SearxngError(`无法连接本地 SearXNG：${safeError(error)}`, { cause: error });
  }
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      throw new SearxngError("SearXNG 拒绝请求；请确认已启用 JSON 格式");
    }
    throw new SearxngError(`SearXNG 请求失败（HTTP ${response.status}）`);
  }
  const raw = await readBoundedResponse(response, 2_000_000, "SearXNG 响应过大");
  let payload: unknown;
  try {
    payload = JSON.parse(new TextDecoder().decode(raw));
  } catch (error) {
    throw new SearxngError("SearXNG 没有返回有效 JSON；请启用 format=json", { cause: error });
  }
  return {
    query,
    results: parseSearxngResults(payload, options.limit),
    meta: { backend: "searxng", duration_ms: Math.round(performance.now() - started) },
  };
}

export function parseSearxngResults(payload: unknown, limit: number): SearxngResult[] {
  if (!isRecord(payload) || !Array.isArray(payload.results)) {
    throw new SearxngError("SearXNG JSON 缺少 results 列表");
  }
  const results: SearxngResult[] = [];
  const seen = new Set<string>();
  for (const item of payload.results) {
    if (!isRecord(item) || typeof item.url !== "string") continue;
    if (!(item.url.startsWith("http://") || item.url.startsWith("https://")) || seen.has(item.url)) continue;
    seen.add(item.url);
    results.push({
      title: typeof item.title === "string" ? item.title.trim() : "",
      url: item.url,
      snippet: typeof item.content === "string" ? item.content.trim() : "",
      engine: typeof item.engine === "string" ? item.engine.trim() : "",
    });
    if (results.length >= limit) break;
  }
  return results;
}

export async function readBoundedResponse(response: Response, maximum: number, message: string): Promise<Uint8Array> {
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > maximum) {
      await reader.cancel();
      throw new SearxngError(message);
    }
    chunks.push(value);
  }
  const merged = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return merged;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeError(error: unknown): string {
  return error instanceof Error ? error.message.slice(0, 500) : String(error).slice(0, 500);
}
