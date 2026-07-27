import type { Browser, Page } from "playwright";

import { validatePublicUrl } from "../web/extract.js";
import type { ToolRegistry } from "../registry.js";

export class BrowserInspector {
  #browser: Browser | undefined;
  #page: Page | undefined;
  readonly #consoleMessages: Array<{ source: string; text: string }> = [];

  async navigate(url: string): Promise<Record<string, unknown>> {
    const safeUrl = await validatePublicUrl(url);
    const page = await this.#ensurePage();
    const response = await page.goto(safeUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
    return { url: page.url(), title: await page.title(), status: response?.status() ?? null };
  }

  async snapshot(maxCharacters: number): Promise<Record<string, unknown>> {
    const page = this.#requiredPage();
    const text = await page.locator("body").innerText({ timeout: 10_000 });
    return { url: page.url(), text: text.slice(0, maxCharacters), truncated: text.length > maxCharacters };
  }

  async console(operation: "messages" | "links", maxLinks: number, clear: boolean): Promise<Record<string, unknown>> {
    const page = this.#requiredPage();
    if (operation === "links") {
      const links = await page.locator("a").evaluateAll((anchors) => anchors.map((anchor) => ({
        text: ((anchor as HTMLElement).innerText || anchor.textContent || "").trim(),
        href: (anchor as HTMLAnchorElement).href || "",
      })));
      const publicLinks: Array<{ text: string; href: string }> = [];
      const seen = new Set<string>();
      for (const item of links) {
        let absolute: string;
        try {
          absolute = new URL(item.href, page.url()).toString();
        } catch {
          continue;
        }
        if (!(absolute.startsWith("http://") || absolute.startsWith("https://")) || seen.has(absolute)) continue;
        seen.add(absolute);
        publicLinks.push({ text: item.text.slice(0, 500), href: absolute });
        if (publicLinks.length >= maxLinks) break;
      }
      return { url: page.url(), links: publicLinks, truncated: publicLinks.length >= maxLinks };
    }
    const result = { url: page.url(), console_messages: [...this.#consoleMessages] };
    if (clear) this.#consoleMessages.length = 0;
    return result;
  }

  async close(): Promise<void> {
    await this.#browser?.close();
    this.#browser = undefined;
    this.#page = undefined;
  }

  async #ensurePage(): Promise<Page> {
    if (this.#page) return this.#page;
    let playwright: typeof import("playwright");
    try {
      playwright = await import("playwright");
    } catch (error) {
      throw new Error("Browser 后端未安装；请安装 playwright 并执行 playwright install chromium", { cause: error });
    }
    this.#browser = await playwright.chromium.launch({ headless: true });
    const page = await this.#browser.newPage();
    await page.route("**/*", async (route) => {
      const request = route.request();
      if (request.isNavigationRequest() && request.frame() === page.mainFrame()) {
        try {
          await validatePublicUrl(request.url());
        } catch {
          await route.abort("blockedbyclient");
          return;
        }
      }
      await route.continue();
    });
    page.on("console", (message) => this.#appendConsole("console", `${message.type()}: ${message.text()}`));
    page.on("pageerror", (error) => this.#appendConsole("pageerror", String(error)));
    this.#page = page;
    return page;
  }

  #requiredPage(): Page {
    if (!this.#page) throw new Error("尚未导航页面，请先调用 browser_navigate");
    return this.#page;
  }

  #appendConsole(source: string, text: string): void {
    this.#consoleMessages.push({ source, text: text.slice(0, 2_000) });
    if (this.#consoleMessages.length > 100) this.#consoleMessages.splice(0, this.#consoleMessages.length - 100);
  }
}

export function registerBrowserTools(registry: ToolRegistry, inspector: BrowserInspector): void {
  registry.register({
    name: "browser_navigate",
    description: "在项目受控浏览器中打开公开网页。",
    inputSchema: { type: "object", properties: { url: { type: "string", minLength: 8, maxLength: 2_000 } }, required: ["url"], additionalProperties: false },
    handler: (arguments_) => inspector.navigate(onlyUrl(arguments_)),
  });
  registry.register({
    name: "browser_snapshot",
    description: "返回当前页面的文本快照，不执行任意 JavaScript。",
    inputSchema: { type: "object", properties: { max_characters: { type: "integer", minimum: 500, maximum: 30_000 } }, additionalProperties: false },
    handler: (arguments_) => inspector.snapshot(maxCharacters(arguments_)),
  });
  registry.register({
    name: "browser_console",
    description: "读取当前页面的 console/JS 错误，或以只读方式列出页面锚点文本与 href。先调用 browser_navigate；不执行模型提供的任意 JavaScript。",
    inputSchema: { type: "object", properties: { operation: { type: "string", enum: ["messages", "links"] }, max_links: { type: "integer", minimum: 1, maximum: 500 }, clear: { type: "boolean" } }, additionalProperties: false },
    handler: (arguments_) => {
      const [operation, maxLinks, clear] = consoleArguments(arguments_);
      return inspector.console(operation, maxLinks, clear);
    },
  });
}

function onlyUrl(arguments_: Record<string, unknown>): string {
  if (Object.keys(arguments_).length !== 1 || typeof arguments_.url !== "string") throw new Error("browser_navigate 只接受 url");
  return arguments_.url;
}

function maxCharacters(arguments_: Record<string, unknown>): number {
  const value = arguments_.max_characters ?? 12_000;
  if (Object.keys(arguments_).some((key) => key !== "max_characters") || typeof value !== "number"
    || !Number.isInteger(value) || value < 500 || value > 30_000) throw new Error("browser_snapshot 参数无效");
  return value;
}

function consoleArguments(arguments_: Record<string, unknown>): ["messages" | "links", number, boolean] {
  if (Object.keys(arguments_).some((key) => !["operation", "max_links", "clear"].includes(key))) throw new Error("browser_console 参数无效");
  const operation = arguments_.operation ?? "messages";
  const maxLinks = arguments_.max_links ?? 100;
  const clear = arguments_.clear ?? false;
  if (operation !== "messages" && operation !== "links") throw new Error("browser_console.operation 必须为 messages 或 links");
  if (typeof maxLinks !== "number" || !Number.isInteger(maxLinks) || maxLinks < 1 || maxLinks > 500) throw new Error("browser_console.max_links 必须在 1 到 500 之间");
  if (typeof clear !== "boolean") throw new Error("browser_console.clear 必须为布尔值");
  return [operation, maxLinks, clear];
}
