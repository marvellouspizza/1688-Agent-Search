import { spawnSync } from "node:child_process";
import { createInterface } from "node:readline/promises";

import { installCodexRuntimeMcp, parseCodexRuntime } from "./codex-runtime.js";
import {
  CODEX_PROVIDER,
  CODEX_RUNTIME_APP_SERVER,
  MODEL_ENV,
  OPENAI_PROVIDER,
  PROVIDER_ENV,
  SUPPORTED_PROVIDERS,
  getPurchaseConfigPath,
  loadPurchaseConfig,
  resolvedDatabasePath,
  savePurchaseConfig,
  withCodexRuntime,
  withPurchaseModel,
  withPurchaseProvider,
  type PurchaseConfig,
} from "./config.js";
import {
  OPENAI_API_KEY_ENV,
  deleteOpenAiApiKey,
  loadOpenAiApiKey,
  saveOpenAiApiKey,
} from "./credentials.js";
import { HermesThinkingSpinner, type SpinnerOutput } from "./display.js";
import type { ChatStatus, ModelOption, ProviderRuntime } from "./models.js";
import {
  listProviderModels,
  PurchaseProviderError,
  resolvePurchaseProvider,
} from "./providers/index.js";
import { createPurchaseAgent, type PurchaseAgentRuntime } from "./runtime.js";
import { PurchaseSessionStore } from "./session-store.js";
import { runMcpServer } from "./tools/mcp-server.js";

export interface CliWritable {
  readonly isTTY?: boolean;
  write(chunk: string): unknown;
}

export interface CliDependencies {
  readonly stdin?: NodeJS.ReadableStream & { isTTY?: boolean; setRawMode?(mode: boolean): void };
  readonly stdout?: CliWritable;
  readonly stderr?: CliWritable;
  readonly environ?: Readonly<Record<string, string | undefined>>;
  readonly cwd?: string;
  readonly question?: (prompt: string) => Promise<string>;
  readonly secretQuestion?: (prompt: string) => Promise<string>;
}

interface CliContext {
  readonly stdin: NodeJS.ReadableStream & { isTTY?: boolean; setRawMode?(mode: boolean): void };
  readonly stdout: CliWritable;
  readonly stderr: CliWritable;
  readonly environ: Readonly<Record<string, string | undefined>>;
  readonly cwd: string;
  readonly question?: (prompt: string) => Promise<string>;
  readonly secretQuestion?: (prompt: string) => Promise<string>;
}

interface ParsedArguments {
  readonly command: string;
  readonly values: Readonly<Record<string, string | boolean | undefined>>;
}

const USAGE = `1688 智能采购项目的 GPT 对话 CLI

Usage:
  as1688 chat [-q QUESTION] [-s SESSION] [-m MODEL]
  as1688 model [--list | --set MODEL | --login | --status]
  as1688 provider [--list | --set PROVIDER | --status | --update-key | --delete-key]
  as1688 sessions [--limit NUMBER]
`;

const PROVIDER_NAMES: Record<string, string> = {
  [CODEX_PROVIDER]: "Local Codex / ChatGPT",
  [OPENAI_PROVIDER]: "OpenAI API",
};

export function formatWelcomeScreen(provider?: string, model?: string, session?: string): string {
  const width = 50;
  const line = (content = ""): string => {
    const shortened = content.length > width ? `${content.slice(0, width - 3)}...` : content;
    return `|${shortened.padEnd(width)}|`;
  };
  return [
    `+${"-".repeat(width)}+`, line(), line("       /\\_/\\        AGENT SEARCH 1688"),
    line("      ( o.o )       Smart Sourcing Assistant"), line("       > ^ <"), line(),
    line(`  Provider: ${provider ?? "Not configured"}`),
    line(`  Model   : ${model ?? "Not configured"}`),
    line(`  Session : ${session ?? "Waiting for model"}`), line("  Help    : /help"), line(),
    `+${"-".repeat(width)}+`,
  ].join("\n");
}

export async function runPurchaseCli(argv: readonly string[] = process.argv.slice(2), dependencies: CliDependencies = {}): Promise<number> {
  const context = buildContext(dependencies);
  const raw = argv.length > 0 ? [...argv] : ["chat"];
  if (raw.includes("--help") || raw.includes("-h")) {
    write(context.stdout, USAGE);
    return 0;
  }
  let parsed: ParsedArguments;
  try { parsed = parseArguments(raw); }
  catch (error) {
    writeLine(context.stderr, error instanceof Error ? error.message : String(error));
    return 2;
  }
  try {
    if (parsed.command === "chat") return await runChat(parsed.values, context);
    if (parsed.command === "model") return await runModel(parsed.values, context);
    if (parsed.command === "provider") return await runProvider(parsed.values, context);
    if (parsed.command === "sessions") return runSessions(parsed.values, context);
    if (parsed.command === "mcp-server") return await runMcpServer({ input: context.stdin, output: context.stdout, cwd: context.cwd });
    writeLine(context.stderr, `未知命令：${parsed.command}`);
    return 2;
  } catch (error) {
    writeLine(context.stderr, `错误：${error instanceof Error ? error.message : String(error)}`);
    return 1;
  }
}

async function runChat(values: ParsedArguments["values"], context: CliContext): Promise<number> {
  let agent: PurchaseAgentRuntime | undefined;
  try {
    let config = loadPurchaseConfig({ environ: context.environ });
    const cliModel = stringValue(values.model);
    let provider = config.provider ?? context.environ[PROVIDER_ENV];
    let model = cliModel ?? config.model ?? context.environ[MODEL_ENV];
    let runtime: ProviderRuntime;
    let models: ModelOption[];
    if (!provider || !model) {
      if (!stringValue(values.question)) {
        writeLine(context.stdout, formatWelcomeScreen(provider ? providerName(provider) : undefined, model));
        writeLine(context.stdout, "\n首次使用，请先选择供应商和默认模型。");
      }
      const setup = await configureProviderAndModel(config, provider, context);
      if (!setup) {
        writeLine(context.stdout, "配置尚未完成。以后可运行：as1688 provider");
        return 0;
      }
      ({ config, runtime, models } = setup);
      provider = runtime.provider;
      model = runtime.model;
    } else {
      ({ runtime, models } = await resolveProviderAndModels(config, provider, context, cliModel));
    }
    if (!models.some((option) => option.model === runtime.model)) {
      throw new PurchaseProviderError(`模型不在当前供应商可用目录中：${runtime.model}。请运行：as1688 model`);
    }
    const store = new PurchaseSessionStore(resolvedDatabasePath(config, { environ: context.environ }));
    agent = createPurchaseAgent({ config, providerRuntime: runtime, sessionStore: store, cwd: context.cwd });
    const session = agent.createOrRestoreSession(stringValue(values.session));
    const question = stringValue(values.question);
    if (question !== undefined) {
      if (!question.trim()) {
        writeLine(context.stderr, "问题不能为空。");
        return 1;
      }
      return (await askAgent(agent, question, context)) === "completed" ? 0 : 1;
    }

    writeLine(context.stdout, formatWelcomeScreen(
      providerName(runtime.provider),
      displayModelName(models, runtime.model),
      values.session ? session.id : "New conversation",
    ));
    const prompts = createInterface({ input: context.stdin, output: context.stdout as NodeJS.WritableStream });
    try {
      while (true) {
        let text: string;
        try { text = await prompts.question("\n你 > "); }
        catch { writeLine(context.stdout, ""); break; }
        if (!text.trim()) continue;
        if (text.startsWith("/")) {
          if (!await routeChatCommand(text.trim(), agent, context, (prompt) => prompts.question(prompt))) break;
        } else {
          await askAgent(agent, text, context);
        }
      }
    } finally {
      prompts.close();
    }
    return 0;
  } catch (error) {
    writeLine(context.stderr, `启动失败：${error instanceof Error ? error.message : String(error)}`);
    return 1;
  } finally {
    if (agent) await agent.close();
  }
}

async function runProvider(values: ParsedArguments["values"], context: CliContext): Promise<number> {
  const config = loadPurchaseConfig({ environ: context.environ });
  if (values.updateKey) return await updateOpenAiKey(config, context);
  if (values.deleteKey) {
    const removed = deleteOpenAiApiKey({ environ: context.environ });
    writeLine(context.stdout, removed.length > 0
      ? `已删除 OpenAI API Key：${removed.join("、")}`
      : "没有找到由 as1688 保存的 OpenAI API Key。");
    if (context.environ[OPENAI_API_KEY_ENV]?.trim()) writeLine(context.stdout, `注意：环境变量 ${OPENAI_API_KEY_ENV} 仍然有效；如需停用，请执行 unset ${OPENAI_API_KEY_ENV}。`);
    return 0;
  }
  const current = config.provider ?? context.environ[PROVIDER_ENV];
  if (values.list) { printProviders(current, context); return 0; }
  if (values.status) {
    writeLine(context.stdout, `当前供应商：${current ? providerName(current) : "尚未配置"}`);
    writeLine(context.stdout, `供应商 ID：${current ?? "尚未配置"}`);
    writeLine(context.stdout, `默认模型：${config.model ?? "尚未配置"}`);
    return 0;
  }
  await configureProviderAndModel(config, stringValue(values.set), context);
  return 0;
}

async function runModel(values: ParsedArguments["values"], context: CliContext): Promise<number> {
  if (values.login) {
    const result = spawnSync("codex", ["login"], { stdio: "inherit" });
    return result.status ?? 1;
  }
  const config = loadPurchaseConfig({ environ: context.environ });
  const provider = config.provider ?? context.environ[PROVIDER_ENV];
  if (!provider) {
    writeLine(context.stdout, "尚未选择供应商，请先选择供应商。");
    await configureProviderAndModel(config, undefined, context);
    return 0;
  }
  const { runtime, models } = await resolveProviderAndModels(config, provider, context);
  if (values.status) {
    writeLine(context.stdout, "绑定状态：已配置");
    writeLine(context.stdout, `凭证来源：${runtime.credentialSource}`);
    writeLine(context.stdout, `Provider：${providerName(runtime.provider)}`);
    writeLine(context.stdout, `默认模型：${config.model ?? context.environ[MODEL_ENV] ?? "尚未配置"}`);
    writeLine(context.stdout, `配置文件：${getPurchaseConfigPath({ environ: context.environ })}`);
    return 0;
  }
  if (values.list) { printModels(models, config.model ?? context.environ[MODEL_ENV], context); return 0; }
  let selected = stringValue(values.set);
  if (selected && !models.some((option) => option.model === selected)) {
    writeLine(context.stderr, `错误：模型不在当前供应商可用目录中：${selected}`);
    printModels(models, runtime.model, context);
    return 1;
  }
  selected ??= await chooseModel(models, config.model ?? context.environ[MODEL_ENV], context);
  if (!selected) return 0;
  const path = savePurchaseConfig(withPurchaseModel(config, selected), { environ: context.environ });
  writeLine(context.stdout, `默认模型已保存：${selected}`);
  writeLine(context.stdout, `配置文件：${path}`);
  return 0;
}

function runSessions(values: ParsedArguments["values"], context: CliContext): number {
  let store: PurchaseSessionStore | undefined;
  try {
    const config = loadPurchaseConfig({ environ: context.environ });
    store = new PurchaseSessionStore(resolvedDatabasePath(config, { environ: context.environ }));
    const limit = Math.max(1, Number(stringValue(values.limit) ?? 20));
    const sessions = store.listSessions(limit);
    if (sessions.length === 0) writeLine(context.stdout, "还没有保存过 Session。");
    else sessions.forEach((session) => writeLine(context.stdout, `${session.id}  ${session.provider}  ${session.model}  更新时间：${session.updatedAt}`));
    return 0;
  } catch (error) {
    writeLine(context.stderr, `读取 Session 失败：${error instanceof Error ? error.message : String(error)}`);
    return 1;
  } finally {
    store?.close();
  }
}

async function resolveProviderAndModels(
  config: PurchaseConfig,
  provider: string,
  context: CliContext,
  cliModel?: string,
): Promise<{ runtime: ProviderRuntime; models: ModelOption[] }> {
  let credentialOverride: string | undefined;
  let saveCredential = false;
  if (provider === OPENAI_PROVIDER) {
    const loaded = loadOpenAiApiKey({ environ: context.environ });
    if (!loaded.apiKey) {
      credentialOverride = (await askSecret("请输入 OpenAI API Key（输入不会显示）：", context)).trim() || undefined;
      if (!credentialOverride) throw new PurchaseProviderError("尚未输入 OpenAI API Key。");
      saveCredential = true;
    }
  }
  let runtime = resolvePurchaseProvider(config, {
    cliProvider: provider,
    environ: context.environ,
    ...(cliModel ? { cliModel } : {}),
    ...(credentialOverride ? { credentialOverride } : {}),
  });
  const models = await listProviderModels(runtime, config.requestTimeoutSeconds);
  if (saveCredential && credentialOverride) {
    const source = saveOpenAiApiKey(credentialOverride, { environ: context.environ });
    runtime = { ...runtime, credentialSource: source };
    writeLine(context.stdout, `OpenAI API Key 已保存到安全凭证存储：${source}`);
  }
  return { runtime, models };
}

async function configureProviderAndModel(
  config: PurchaseConfig,
  requestedProvider: string | undefined,
  context: CliContext,
): Promise<{ config: PurchaseConfig; runtime: ProviderRuntime; models: ModelOption[] } | undefined> {
  const provider = requestedProvider ?? await chooseProvider(config.provider, context);
  if (!provider) return undefined;
  if (!(SUPPORTED_PROVIDERS as readonly string[]).includes(provider)) throw new Error(`不支持的供应商：${provider}`);
  const providerConfig = withPurchaseProvider(config, provider);
  const { runtime: initialRuntime, models } = await resolveProviderAndModels(providerConfig, provider, context);
  const selected = await chooseModel(models, config.provider === provider ? config.model : undefined, context);
  if (!selected) {
    writeLine(context.stdout, "尚未选择模型，供应商配置没有修改。");
    return undefined;
  }
  const configured = withPurchaseProvider(config, provider, selected);
  savePurchaseConfig(configured, { environ: context.environ });
  const runtime = { ...initialRuntime, model: selected };
  writeLine(context.stdout, `供应商已保存：${providerName(provider)}`);
  writeLine(context.stdout, `默认模型已保存：${selected}`);
  return { config: configured, runtime, models };
}

async function updateOpenAiKey(config: PurchaseConfig, context: CliContext): Promise<number> {
  if (context.environ[OPENAI_API_KEY_ENV]?.trim()) {
    writeLine(context.stderr, `当前正在使用环境变量 ${OPENAI_API_KEY_ENV}；它的优先级最高。\n请先执行 unset ${OPENAI_API_KEY_ENV}，再运行此命令。`);
    return 1;
  }
  const apiKey = (await askSecret("请输入 OpenAI API Key（输入不会显示）：", context)).trim();
  if (!apiKey) { writeLine(context.stdout, "API Key 没有修改。"); return 0; }
  const openAiConfig = withPurchaseProvider(config, OPENAI_PROVIDER);
  const runtime = resolvePurchaseProvider(openAiConfig, { cliProvider: OPENAI_PROVIDER, credentialOverride: apiKey, environ: context.environ });
  const models = await listProviderModels(runtime, config.requestTimeoutSeconds);
  const source = saveOpenAiApiKey(apiKey, { environ: context.environ });
  writeLine(context.stdout, `OpenAI API Key 验证成功，可用文本模型：${models.length} 个`);
  writeLine(context.stdout, `OpenAI API Key 已更新：${source}`);
  return 0;
}

async function askAgent(agent: PurchaseAgentRuntime, text: string, context: CliContext): Promise<ChatStatus> {
  let spinner: HermesThinkingSpinner | undefined;
  let responseStarted = false;
  const thinking = (active: boolean): void => {
    if (active && !spinner) {
      if (responseStarted) { writeLine(context.stdout, ""); responseStarted = false; }
      spinner = HermesThinkingSpinner.createForModelRequest(context.stdout as SpinnerOutput);
      spinner.start();
    } else if (!active && spinner) {
      spinner.stop();
      spinner = undefined;
    }
  };
  const delta = (part: string): void => {
    if (!responseStarted) { write(context.stdout, "1688 Agent > "); responseStarted = true; }
    write(context.stdout, part);
  };
  const interrupt = (): void => { void agent.stopReply(); };
  process.once("SIGINT", interrupt);
  try {
    const result = await agent.chat(text, undefined, { onDelta: delta, onThinking: thinking });
    thinking(false);
    if (!responseStarted) write(context.stdout, "1688 Agent > ");
    writeLine(context.stdout, "");
    if (result.status !== "completed") writeLine(context.stdout, `[${result.status}] ${result.error ?? "请求未完成"}`);
    return result.status;
  } finally {
    thinking(false);
    process.removeListener("SIGINT", interrupt);
  }
}

async function routeChatCommand(
  text: string,
  agent: PurchaseAgentRuntime,
  context: CliContext,
  prompt: (message: string) => Promise<string>,
): Promise<boolean> {
  const [command, ...rest] = text.split(/\s+/u);
  const argument = rest.join(" ").trim();
  if (command === "/quit") return false;
  if (command === "/help") {
    writeLine(context.stdout, "\n会话内命令：\n  /model [MODEL]\n  /codex-runtime [auto|on]\n  /session\n  /stop\n  /help\n  /quit\n");
  } else if (command === "/session") {
    writeLine(context.stdout, `当前 Session：${agent.session?.id ?? "未知"}`);
  } else if (command === "/stop") {
    writeLine(context.stdout, "当前没有正在生成的回复；生成过程中可按 Ctrl+C 中止。");
  } else if (command === "/model") {
    const models = await listProviderModels(agent.providerRuntime, agent.config.requestTimeoutSeconds);
    const selected = argument || await chooseModel(models, agent.providerRuntime.model, context, prompt);
    if (selected && models.some((option) => option.model === selected)) {
      agent.switchModel(selected);
      writeLine(context.stdout, `本 Session 已切换到：${selected}`);
    } else if (selected) writeLine(context.stdout, `模型不可用：${selected}`);
  } else if (command === "/codex-runtime") {
    if (!argument) {
      writeLine(context.stdout, `当前 Codex Runtime：${agent.config.openaiRuntime}`);
      writeLine(context.stdout, "auto 不启动 app-server；codex_app_server 会创建 Codex Task。");
    } else if (agent.providerRuntime.provider !== CODEX_PROVIDER) {
      writeLine(context.stdout, "Codex Runtime 只适用于 Local Codex / ChatGPT 供应商。");
    } else {
      const selected = parseCodexRuntime(argument);
      const updated = withCodexRuntime(agent.config, selected);
      if (selected === CODEX_RUNTIME_APP_SERVER) {
        resolvePurchaseProvider(updated, { cliProvider: agent.providerRuntime.provider, cliModel: agent.providerRuntime.model, environ: context.environ });
        const path = installCodexRuntimeMcp({ cwd: context.cwd, environ: context.environ });
        writeLine(context.stdout, `项目工具已注册到 Codex app-server：${path}`);
      }
      savePurchaseConfig(updated, { environ: context.environ });
      const previous = agent.config.openaiRuntime;
      agent.config = updated;
      writeLine(context.stdout, `Codex Runtime：${previous} → ${selected}`);
      writeLine(context.stdout, "下一次新建 Session 生效；当前 Session 保持原 Runtime。");
    }
  } else {
    writeLine(context.stdout, `未知命令：${String(command)}。输入 /help 查看命令。`);
  }
  return true;
}

function parseArguments(raw: readonly string[]): ParsedArguments {
  const command = raw[0] ?? "chat";
  if (!["chat", "model", "provider", "sessions", "mcp-server"].includes(command)) throw new Error(`未知命令：${command}`);
  const definitions: Record<string, { key: string; value: boolean }> = command === "chat"
    ? { "-q": { key: "question", value: true }, "--question": { key: "question", value: true }, "-s": { key: "session", value: true }, "--session": { key: "session", value: true }, "-m": { key: "model", value: true }, "--model": { key: "model", value: true } }
    : command === "model"
      ? { "--list": { key: "list", value: false }, "--set": { key: "set", value: true }, "--login": { key: "login", value: false }, "--status": { key: "status", value: false } }
      : command === "provider"
        ? { "--list": { key: "list", value: false }, "--set": { key: "set", value: true }, "--status": { key: "status", value: false }, "--update-key": { key: "updateKey", value: false }, "--delete-key": { key: "deleteKey", value: false } }
        : command === "sessions" ? { "--limit": { key: "limit", value: true } } : {};
  const values: Record<string, string | boolean> = {};
  for (let index = 1; index < raw.length; index += 1) {
    const definition = definitions[raw[index]!];
    if (!definition) throw new Error(`未知参数：${raw[index]}`);
    if (definition.value) {
      const value = raw[++index];
      if (value === undefined) throw new Error(`参数缺少值：${raw[index - 1]}`);
      values[definition.key] = value;
    } else values[definition.key] = true;
  }
  return { command, values };
}

async function chooseProvider(current: string | undefined, context: CliContext): Promise<string | undefined> {
  printProviders(current, context);
  const answer = (await ask("输入序号选择供应商（直接回车取消）：", context)).trim();
  if (!answer) return undefined;
  const index = Number(answer);
  if (!Number.isInteger(index) || index < 1 || index > SUPPORTED_PROVIDERS.length) {
    writeLine(context.stdout, "请输入列表中的有效数字。");
    return undefined;
  }
  return SUPPORTED_PROVIDERS[index - 1];
}

async function chooseModel(
  models: readonly ModelOption[],
  current: string | undefined,
  context: CliContext,
  prompt?: (message: string) => Promise<string>,
): Promise<string | undefined> {
  printModels(models, current, context);
  if (models.length === 0) return undefined;
  const answer = (await (prompt ?? ((message) => ask(message, context)))("输入序号选择模型（直接回车取消）：")).trim();
  if (!answer) return undefined;
  const index = Number(answer);
  if (!Number.isInteger(index) || index < 1 || index > models.length) {
    writeLine(context.stdout, "请输入列表中的有效数字。");
    return undefined;
  }
  return models[index - 1]?.model;
}

function printProviders(current: string | undefined, context: CliContext): void {
  writeLine(context.stdout, "可用供应商：");
  const descriptions: Record<string, string> = {
    [CODEX_PROVIDER]: "复用本机 Codex 的 ChatGPT 登录",
    [OPENAI_PROVIDER]: "使用你自己的 OpenAI API Key",
  };
  SUPPORTED_PROVIDERS.forEach((provider, index) => {
    writeLine(context.stdout, `  ${index + 1}. ${providerName(provider)} [${provider}]${provider === current ? " ← 当前" : ""}`);
    writeLine(context.stdout, `     ${descriptions[provider]}`);
  });
}

function printModels(models: readonly ModelOption[], current: string | undefined, context: CliContext): void {
  if (models.length === 0) { writeLine(context.stdout, "没有读取到可用模型。"); return; }
  writeLine(context.stdout, "可用模型：");
  models.forEach((option, index) => {
    writeLine(context.stdout, `  ${index + 1}. ${option.displayName}  [${option.model}]${option.isDefault ? "（Codex 默认）" : ""}${option.model === current ? " ← 当前" : ""}`);
    if (option.description) writeLine(context.stdout, `     ${option.description}`);
  });
}

function displayModelName(models: readonly ModelOption[], model: string): string {
  return models.find((option) => option.model === model)?.displayName ?? model;
}

function providerName(provider: string): string {
  return PROVIDER_NAMES[provider] ?? provider;
}

function buildContext(dependencies: CliDependencies): CliContext {
  const base = {
    stdin: dependencies.stdin ?? process.stdin,
    stdout: dependencies.stdout ?? process.stdout,
    stderr: dependencies.stderr ?? process.stderr,
    environ: dependencies.environ ?? process.env,
    cwd: dependencies.cwd ?? process.cwd(),
  };
  return {
    ...base,
    ...(dependencies.question ? { question: dependencies.question } : {}),
    ...(dependencies.secretQuestion ? { secretQuestion: dependencies.secretQuestion } : {}),
  };
}

async function ask(prompt: string, context: CliContext): Promise<string> {
  if (context.question) return await context.question(prompt);
  const reader = createInterface({ input: context.stdin, output: context.stdout as NodeJS.WritableStream });
  try { return await reader.question(prompt); }
  finally { reader.close(); }
}

async function askSecret(prompt: string, context: CliContext): Promise<string> {
  if (context.secretQuestion) return await context.secretQuestion(prompt);
  if (!context.stdin.isTTY || !context.stdin.setRawMode) return await ask(prompt, context);
  write(context.stdout, prompt);
  return await new Promise<string>((resolvePromise) => {
    let value = "";
    const input = context.stdin;
    const onData = (chunk: Buffer | string): void => {
      for (const character of String(chunk)) {
        if (character === "\r" || character === "\n") {
          cleanup(); writeLine(context.stdout, ""); resolvePromise(value); return;
        }
        if (character === "\u0003") { cleanup(); writeLine(context.stdout, ""); resolvePromise(""); return; }
        if (character === "\u007f") value = value.slice(0, -1);
        else value += character;
      }
    };
    const cleanup = (): void => { input.removeListener("data", onData); input.setRawMode?.(false); };
    input.setRawMode!(true);
    input.resume();
    input.on("data", onData);
  });
}

function stringValue(value: string | boolean | undefined): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function write(stream: CliWritable, text: string): void {
  stream.write(text);
}

function writeLine(stream: CliWritable, text: string): void {
  stream.write(`${text}\n`);
}
