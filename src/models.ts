export type MessageRole = "system" | "user" | "assistant" | "tool";

export type MessageStatus =
  | "pending"
  | "streaming"
  | "completed"
  | "failed"
  | "interrupted"
  | "incomplete";

export type ChatStatus = "completed" | "failed" | "interrupted" | "incomplete";

export type ConversationState =
  | "idle"
  | "preparing"
  | "requesting"
  | "streaming"
  | "completed"
  | "failed"
  | "interrupted"
  | "incomplete";

export interface Message {
  readonly id: string;
  readonly sessionId: string;
  readonly role: MessageRole;
  readonly content: string;
  readonly status: MessageStatus;
  readonly provider: string;
  readonly model: string;
  readonly createdAt: string;
}

export interface TokenUsage {
  readonly inputTokens: number;
  readonly outputTokens: number;
  readonly totalTokens: number;
}

export const EMPTY_USAGE: TokenUsage = Object.freeze({
  inputTokens: 0,
  outputTokens: 0,
  totalTokens: 0,
});

export interface ChatResult {
  readonly status: ChatStatus;
  readonly sessionId: string;
  readonly messageId: string;
  readonly content: string;
  readonly provider: string;
  readonly model: string;
  readonly usage: TokenUsage;
  readonly error?: string;
}

export interface ProviderRuntime {
  readonly provider: string;
  readonly model: string;
  readonly apiMode: string;
  readonly baseUrl: string;
  readonly credentialSource: string;
  readonly codexPath?: string;
  readonly credential?: string;
}

export interface ModelOption {
  readonly model: string;
  readonly displayName: string;
  readonly description: string;
  readonly isDefault: boolean;
  readonly hidden: boolean;
}

export interface PurchaseSession {
  readonly id: string;
  readonly provider: string;
  readonly model: string;
  readonly providerThreadId?: string;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface ProviderStreamResult {
  readonly content: string;
  readonly usage: TokenUsage;
  readonly actualModel: string;
  readonly providerThreadId: string;
}

export interface ProviderToolCall {
  readonly callId: string;
  readonly name: string;
  readonly arguments: Record<string, unknown>;
}

export interface ProviderTurnResult {
  readonly content: string;
  readonly toolCalls: readonly ProviderToolCall[];
  readonly responseItems: readonly Record<string, unknown>[];
  readonly usage: TokenUsage;
  readonly actualModel: string;
  readonly responseId: string;
  readonly providerThreadId: string;
}

export function tokenUsageFromCodex(value?: Record<string, unknown>): TokenUsage {
  if (value === undefined) {
    return EMPTY_USAGE;
  }
  return {
    inputTokens: numericUsageValue(value.inputTokens),
    outputTokens: numericUsageValue(value.outputTokens),
    totalTokens: numericUsageValue(value.totalTokens),
  };
}

export function validateConversationRoles(messages: readonly Message[]): void {
  let expected: MessageRole = "user";
  for (const message of messages) {
    if (message.status !== "completed") {
      continue;
    }
    if (message.role !== expected) {
      throw new Error(`会话角色顺序错误：期望 ${expected}，实际 ${message.role}`);
    }
    expected = expected === "user" ? "assistant" : "user";
  }
  if (messages.length > 0 && expected === "assistant") {
    throw new Error("会话中存在尚未配对的用户消息");
  }
}

function numericUsageValue(value: unknown): number {
  if (value === undefined || value === null || value === "") {
    return 0;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.trunc(parsed) : 0;
}
