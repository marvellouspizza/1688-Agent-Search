export type JsonSchema = Record<string, unknown>;
export type ToolResult = Record<string, unknown>;

export interface ToolEntry {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: JsonSchema;
  readonly handler: (arguments_: Record<string, unknown>) => ToolResult | Promise<ToolResult>;
  readonly parallelSafe?: boolean;
}

export interface McpToolDefinition {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: JsonSchema;
}

export class ToolRegistry {
  readonly #entries = new Map<string, ToolEntry>();
  readonly #closeHandlers: Array<() => void | Promise<void>> = [];
  #closed = false;

  register(entry: ToolEntry): void {
    if (this.#entries.has(entry.name)) throw new Error(`工具名称重复：${entry.name}`);
    this.#entries.set(entry.name, entry);
  }

  addCloseHandler(handler: () => void | Promise<void>): void {
    this.#closeHandlers.push(handler);
  }

  definitions(): McpToolDefinition[] {
    return [...this.#entries.values()].map((entry) => ({
      name: entry.name,
      description: entry.description,
      inputSchema: entry.inputSchema,
    }));
  }

  async dispatch(name: string, arguments_: Record<string, unknown>): Promise<ToolResult> {
    if (this.#closed) throw new Error("工具注册表已关闭");
    const entry = this.#entries.get(name);
    if (!entry) throw new Error(`未注册工具：${name}`);
    return await entry.handler(arguments_);
  }

  isParallelSafe(name: string): boolean {
    return this.#entries.get(name)?.parallelSafe === true;
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    for (const handler of this.#closeHandlers.reverse()) await handler();
  }
}
