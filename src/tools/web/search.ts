import type { PurchaseConfig } from "../../config.js";
import { loadPurchaseConfig } from "../../config.js";
import { SkillCatalog } from "../../skills/catalog.js";
import { BrowserInspector, registerBrowserTools } from "../browser/inspect.js";
import { ToolRegistry } from "../registry.js";
import { buildWebExtractEntry } from "./extract.js";
import { searchSearxng } from "./searxng.js";

export const WEB_SEARCH_SCHEMA = {
  type: "object",
  properties: {
    query: { type: "string", minLength: 2, maxLength: 300 },
    limit: { type: "integer", minimum: 1, maximum: 20 },
  },
  required: ["query"],
  additionalProperties: false,
} as const;

export interface BuildToolRegistryOptions {
  readonly skillRoot?: string;
  readonly config?: PurchaseConfig;
}

export function buildToolRegistry(options: BuildToolRegistryOptions = {}): ToolRegistry {
  const config = options.config ?? loadPurchaseConfig();
  const registry = new ToolRegistry();
  registry.register({
    name: "web_search",
    description: "使用本机 SearXNG 搜索公开网页。结果是搜索索引，不能证明商品库存、价格或商家资质已经核验。",
    inputSchema: WEB_SEARCH_SCHEMA,
    handler: (arguments_) => webSearchHandler(arguments_, config),
    parallelSafe: true,
  });
  registry.register(buildWebExtractEntry(config));
  const inspector = new BrowserInspector();
  registerBrowserTools(registry, inspector);
  registry.addCloseHandler(() => inspector.close());
  const catalog = new SkillCatalog(options.skillRoot ? [options.skillRoot] : []);
  registry.register({
    name: "skills_list",
    description: "列出本项目已安装的 Skill；不读取本机 Codex Skill。",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    handler: (arguments_) => skillsList(arguments_, catalog),
    parallelSafe: true,
  });
  registry.register({
    name: "skill_view",
    description: "读取一个项目 Skill 或其 references 中的文件。",
    inputSchema: { type: "object", properties: { name: { type: "string" }, path: { type: "string" } }, required: ["name"], additionalProperties: false },
    handler: (arguments_) => skillView(arguments_, catalog),
    parallelSafe: true,
  });
  return registry;
}

export async function webSearchHandler(arguments_: Record<string, unknown>, config: PurchaseConfig): Promise<Record<string, unknown>> {
  const unknown = Object.keys(arguments_).filter((key) => key !== "query" && key !== "limit").sort();
  if (unknown.length > 0) throw new Error(`web_search 不接受额外参数：${unknown.join(", ")}`);
  const limit = arguments_.limit ?? 10;
  if (typeof arguments_.query !== "string" || typeof limit !== "number" || !Number.isInteger(limit)) {
    throw new Error("web_search 参数格式无效");
  }
  return await searchSearxng({
    baseUrl: config.searxngBaseUrl,
    query: arguments_.query,
    limit,
    timeoutSeconds: config.searxngTimeoutSeconds,
  });
}

function skillsList(arguments_: Record<string, unknown>, catalog: SkillCatalog): Record<string, unknown> {
  if (Object.keys(arguments_).length > 0) throw new Error("skills_list 不接受参数");
  return { skills: catalog.list().map(({ name, description }) => ({ name, description })) };
}

function skillView(arguments_: Record<string, unknown>, catalog: SkillCatalog): Record<string, unknown> {
  if (Object.keys(arguments_).some((key) => key !== "name" && key !== "path") || typeof arguments_.name !== "string") {
    throw new Error("skill_view 参数无效");
  }
  if (arguments_.path !== undefined && typeof arguments_.path !== "string") throw new Error("skill_view.path 必须是字符串");
  const path = arguments_.path ?? "SKILL.md";
  return { name: arguments_.name, path, content: catalog.read(arguments_.name, path) };
}
