import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

import {
  APP_HOME_ENV,
  CODEX_RUNTIME_APP_SERVER,
  CODEX_RUNTIME_AUTO,
  SKILL_ROOT_ENV,
  resolveSkillRoot,
  type CodexRuntime,
} from "./config.js";

export const MANAGED_START = "# >>> 1688 Agent Search managed Codex runtime >>>";
export const MANAGED_END = "# <<< 1688 Agent Search managed Codex runtime <<<";

export interface ToolsMcpConfig {
  readonly enabled: boolean;
  readonly command: string;
  readonly args: readonly string[];
  readonly env: Readonly<Record<string, string>>;
  readonly startup_timeout_sec: number;
  readonly tool_timeout_sec: number;
}

export interface McpConfigOptions {
  readonly cwd?: string;
  readonly launcherPath?: string;
  readonly execPath?: string;
  readonly skillRoot?: string;
  readonly environ?: Readonly<Record<string, string | undefined>>;
}

export interface InstallMcpOptions extends McpConfigOptions {
  readonly codexHome?: string;
}

export function parseCodexRuntime(value: string): CodexRuntime {
  const aliases: Record<string, CodexRuntime> = {
    on: CODEX_RUNTIME_APP_SERVER,
    codex: CODEX_RUNTIME_APP_SERVER,
    enable: CODEX_RUNTIME_APP_SERVER,
    off: CODEX_RUNTIME_AUTO,
    default: CODEX_RUNTIME_AUTO,
    disable: CODEX_RUNTIME_AUTO,
    hermes: CODEX_RUNTIME_AUTO,
  };
  const normalized = value.trim().toLowerCase();
  const resolved = aliases[normalized] ?? normalized;
  if (resolved !== CODEX_RUNTIME_AUTO && resolved !== CODEX_RUNTIME_APP_SERVER) {
    throw new Error("Runtime 必须是 auto 或 codex_app_server（也可使用 on/off）");
  }
  return resolved;
}

export function buildToolsMcpConfig(options: McpConfigOptions = {}): ToolsMcpConfig {
  const environment = options.environ ?? process.env;
  const launcherPath = resolve(options.launcherPath ?? process.argv[1] ?? join(process.cwd(), "dist", "cli-entry.js"));
  const env: Record<string, string> = {
    [SKILL_ROOT_ENV]: resolve(options.skillRoot ?? resolveSkillRoot(options.cwd, { environ: environment })),
  };
  const configuredHome = environment[APP_HOME_ENV]?.trim();
  if (configuredHome) env[APP_HOME_ENV] = configuredHome;
  return {
    enabled: true,
    command: options.execPath ?? process.execPath,
    args: [launcherPath, "mcp-server"],
    env,
    startup_timeout_sec: 15,
    tool_timeout_sec: 45,
  };
}

export function replaceManagedCodexBlock(existing: string, config: ToolsMcpConfig): string {
  const preserved = stripManagedCodexBlock(existing);
  let content = preserved;
  if (content && !content.endsWith("\n")) content += "\n";
  if (content) content += "\n";
  return content + managedBlock(config);
}

export function installCodexRuntimeMcp(options: InstallMcpOptions = {}): string {
  const environment = options.environ ?? process.env;
  const configuredHome = environment.CODEX_HOME?.trim();
  const codexHome = resolve(options.codexHome ?? (configuredHome || join(homedir(), ".codex")));
  const target = join(codexHome, "config.toml");
  try {
    const existing = existsSync(target) ? readFileSync(target, "utf8") : "";
    const content = replaceManagedCodexBlock(existing, buildToolsMcpConfig(options));
    mkdirSync(codexHome, { recursive: true, mode: 0o700 });
    const temporary = join(codexHome, `.config.toml.as1688.${process.pid}.${Date.now()}`);
    try {
      writeFileSync(temporary, content, { encoding: "utf8", mode: 0o600, flag: "wx" });
      chmodSync(temporary, existsSync(target) ? statSync(target).mode & 0o777 : 0o600);
      renameSync(temporary, target);
    } catch (error) {
      rmSync(temporary, { force: true });
      throw error;
    }
  } catch (error) {
    throw new Error(`无法注册 1688-tools MCP：${error instanceof Error ? error.message : String(error)}`, { cause: error });
  }
  return target;
}

function managedBlock(config: ToolsMcpConfig): string {
  const lines = [MANAGED_START, "", '[mcp_servers."1688-tools"]'];
  for (const [key, value] of Object.entries(config)) lines.push(`${key} = ${tomlValue(value)}`);
  lines.push("", MANAGED_END, "");
  return lines.join("\n");
}

function stripManagedCodexBlock(text: string): string {
  if (text.includes(MANAGED_START) !== text.includes(MANAGED_END)) {
    throw new Error("Codex 配置中的 as1688 托管区块不完整");
  }
  const start = text.indexOf(MANAGED_START);
  if (start < 0) return text.trimEnd() + (text.trim() ? "\n" : "");
  const endMarker = text.indexOf(MANAGED_END, start);
  const after = endMarker + MANAGED_END.length;
  const stripped = `${text.slice(0, start)}${text.slice(after).replace(/^\s*\n?/u, "")}`.trimEnd();
  return stripped ? `${stripped}\n` : "";
}

function tomlValue(value: unknown): string {
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(tomlValue).join(", ")}]`;
  if (typeof value === "object" && value !== null) {
    return `{ ${Object.entries(value).map(([key, item]) => `${JSON.stringify(key)} = ${tomlValue(item)}`).join(", ")} }`;
  }
  throw new Error(`不能写入 Codex TOML 的值：${typeof value}`);
}
