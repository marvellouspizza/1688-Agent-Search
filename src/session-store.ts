import {
  chmodSync,
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { dirname, join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import {
  type Message,
  type MessageRole,
  type MessageStatus,
  type ProviderRuntime,
  type PurchaseSession,
  type TokenUsage,
  validateConversationRoles,
} from "./models.js";

type SqlRow = Record<string, unknown>;

interface OwnedLock {
  readonly path: string;
  readonly descriptor: number;
}

interface BeginRequestResult {
  readonly userMessage: Message;
  readonly requestId: string;
}

interface ToolTraceInput {
  readonly sessionId: string;
  readonly requestId: string;
  readonly sequence: number;
  readonly callId: string;
  readonly name: string;
  readonly argumentsJson: string;
  readonly resultJson: string;
  readonly status: "completed" | "failed";
  readonly durationMs: number;
}

interface SaveReplyInput {
  readonly sessionId: string;
  readonly requestId: string;
  readonly content: string;
  readonly providerRuntime: ProviderRuntime;
  readonly actualModel: string;
  readonly usage: TokenUsage;
  readonly providerThreadId: string;
}

interface FailRequestInput {
  readonly requestId: string;
  readonly userMessageId: string;
  readonly status: Extract<MessageStatus, "failed" | "interrupted" | "incomplete">;
  readonly error: string;
}

export class PurchaseSessionStore {
  readonly databasePath: string;
  readonly ownerId: string;
  readonly #database: DatabaseSync;
  readonly #sessionLocks = new Map<string, OwnedLock>();
  #ownerLock: OwnedLock | undefined;
  #closed = false;
  readonly #exitCleanup: () => void;

  constructor(databasePath: string) {
    this.databasePath = databasePath;
    this.ownerId = compactUuid();
    mkdirSync(dirname(databasePath), { recursive: true, mode: 0o700 });
    this.#database = new DatabaseSync(databasePath);
    this.#database.exec("PRAGMA foreign_keys = ON");
    this.#initializeDatabase();
    this.#acquireOwnerLock();
    this.#recoverInterruptedProcesses();
    chmodSync(databasePath, 0o600);
    this.#exitCleanup = () => this.#releaseLocks();
    process.once("exit", this.#exitCleanup);
  }

  createOrRestoreSession(
    sessionId: string | undefined,
    providerRuntime: ProviderRuntime,
  ): PurchaseSession {
    this.#assertOpen();
    const resolvedId = sessionId ?? `session_${compactUuid()}`;
    let row = this.#database.prepare("SELECT * FROM sessions WHERE id = ?").get(resolvedId) as SqlRow | undefined;
    if (row === undefined) {
      const timestamp = nowShanghai();
      this.#database.prepare(`
        INSERT INTO sessions(id, provider, model, provider_thread_id, created_at, updated_at)
        VALUES (?, ?, ?, NULL, ?, ?)
      `).run(resolvedId, providerRuntime.provider, providerRuntime.model, timestamp, timestamp);
      row = this.#database.prepare("SELECT * FROM sessions WHERE id = ?").get(resolvedId) as SqlRow | undefined;
    }
    if (row === undefined) throw new Error(`无法创建 Session：${resolvedId}`);
    return sessionFromRow(row);
  }

  getSession(sessionId: string): PurchaseSession {
    this.#assertOpen();
    const row = this.#database.prepare("SELECT * FROM sessions WHERE id = ?").get(sessionId) as SqlRow | undefined;
    if (row === undefined) throw new Error(`Session 不存在：${sessionId}`);
    return sessionFromRow(row);
  }

  acquireSessionLock(sessionId: string): void {
    this.#assertOpen();
    if (this.#sessionLocks.has(sessionId)) return;
    mkdirSync(this.#sessionLockDirectory, { recursive: true, mode: 0o700 });
    const digest = createHash("sha256").update(sessionId, "utf8").digest("hex");
    const path = join(this.#sessionLockDirectory, `${digest}.lock`);
    let lock: OwnedLock | undefined;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        lock = createOwnedLock(path, this.ownerId);
        break;
      } catch (error) {
        if (!isAlreadyExists(error) || isLockProcessAlive(path)) {
          throw new Error(`Session 正在另一个 CLI 中使用：${sessionId}`, { cause: error });
        }
        rmSync(path, { force: true });
      }
    }
    if (lock === undefined) {
      throw new Error(`Session 正在另一个 CLI 中使用：${sessionId}`);
    }
    this.#sessionLocks.set(sessionId, lock);
  }

  attachProviderThread(sessionId: string, providerThreadId: string, model: string): void {
    this.#assertOpen();
    this.#database.prepare(`
      UPDATE sessions
      SET provider_thread_id = ?, model = ?, updated_at = ?
      WHERE id = ?
    `).run(providerThreadId, model, nowShanghai(), sessionId);
  }

  loadContextMessages(sessionId: string): Message[] {
    this.#assertOpen();
    const rows = this.#database.prepare(`
      SELECT *
      FROM messages
      WHERE session_id = ?
        AND status = 'completed'
        AND role IN ('user', 'assistant')
        AND (
          role = 'assistant'
          OR EXISTS (
            SELECT 1
            FROM requests
            WHERE requests.user_message_id = messages.id
              AND requests.status = 'completed'
          )
        )
      ORDER BY created_at, rowid
    `).all(sessionId) as SqlRow[];
    const messages = rows.map(messageFromRow);
    validateConversationRoles(messages);
    return messages;
  }

  beginRequest(
    sessionId: string,
    userInput: string,
    providerRuntime: ProviderRuntime,
  ): BeginRequestResult {
    this.#assertOpen();
    const userMessage: Message = {
      id: `msg_${compactUuid()}`,
      sessionId,
      role: "user",
      content: userInput,
      status: "completed",
      provider: providerRuntime.provider,
      model: providerRuntime.model,
      createdAt: nowShanghai(),
    };
    const requestId = `request_${compactUuid()}`;
    this.#transaction(() => {
      this.#database.prepare(`
        INSERT INTO messages(id, session_id, role, content, status, provider, model, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        userMessage.id,
        userMessage.sessionId,
        userMessage.role,
        userMessage.content,
        userMessage.status,
        userMessage.provider,
        userMessage.model,
        userMessage.createdAt,
      );
      this.#database.prepare(`
        INSERT INTO requests(id, session_id, user_message_id, status, provider, model, owner_id, started_at)
        VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
      `).run(
        requestId,
        sessionId,
        userMessage.id,
        providerRuntime.provider,
        providerRuntime.model,
        this.ownerId,
        nowShanghai(),
      );
    });
    return { userMessage, requestId };
  }

  markRequestStreaming(requestId: string): void {
    this.#assertOpen();
    this.#database.prepare("UPDATE requests SET status = 'streaming' WHERE id = ?").run(requestId);
  }

  appendToolTrace(input: ToolTraceInput): void {
    this.#assertOpen();
    this.#database.prepare(`
      INSERT INTO tool_traces(
        id, session_id, request_id, sequence, call_id, name,
        arguments_json, result_json, status, duration_ms, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      `tool_${compactUuid()}`,
      input.sessionId,
      input.requestId,
      input.sequence,
      input.callId,
      input.name,
      input.argumentsJson.slice(0, 30_000),
      input.resultJson.slice(0, 30_000),
      input.status,
      Math.max(0, Math.trunc(input.durationMs)),
      nowShanghai(),
    );
  }

  saveReply(input: SaveReplyInput): Message {
    this.#assertOpen();
    const assistant: Message = {
      id: `msg_${compactUuid()}`,
      sessionId: input.sessionId,
      role: "assistant",
      content: input.content,
      status: "completed",
      provider: input.providerRuntime.provider,
      model: input.actualModel,
      createdAt: nowShanghai(),
    };
    this.#transaction(() => {
      this.#database.prepare(`
        INSERT INTO messages(id, session_id, role, content, status, provider, model, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        assistant.id,
        assistant.sessionId,
        assistant.role,
        assistant.content,
        assistant.status,
        assistant.provider,
        assistant.model,
        assistant.createdAt,
      );
      this.#database.prepare(`
        UPDATE requests
        SET status = 'completed', model = ?, input_tokens = ?, output_tokens = ?,
            total_tokens = ?, completed_at = ?
        WHERE id = ?
      `).run(
        input.actualModel,
        input.usage.inputTokens,
        input.usage.outputTokens,
        input.usage.totalTokens,
        nowShanghai(),
        input.requestId,
      );
      this.#database.prepare(`
        UPDATE sessions
        SET model = ?, provider_thread_id = ?, updated_at = ?
        WHERE id = ?
      `).run(input.actualModel, input.providerThreadId, nowShanghai(), input.sessionId);
    });
    return assistant;
  }

  failRequest(input: FailRequestInput): void {
    this.#assertOpen();
    const completedAt = nowShanghai();
    this.#transaction(() => {
      this.#database.prepare("UPDATE messages SET status = ? WHERE id = ?")
        .run(input.status, input.userMessageId);
      this.#database.prepare(`
        UPDATE requests
        SET status = ?, error = ?, completed_at = ?
        WHERE id = ?
      `).run(input.status, input.error.slice(0, 1_000), completedAt, input.requestId);
    });
  }

  listSessions(limit = 20): PurchaseSession[] {
    this.#assertOpen();
    if (!Number.isInteger(limit) || limit <= 0) throw new Error("Session 数量必须大于 0");
    const rows = this.#database.prepare(`
      SELECT * FROM sessions ORDER BY updated_at DESC, rowid DESC LIMIT ?
    `).all(limit) as SqlRow[];
    return rows.map(sessionFromRow);
  }

  close(): void {
    if (this.#closed) return;
    this.#closed = true;
    process.removeListener("exit", this.#exitCleanup);
    this.#releaseLocks();
    this.#database.close();
  }

  get #ownerLockDirectory(): string {
    return join(dirname(this.databasePath), `.${this.databasePath.split(/[\\/]/).at(-1) ?? "sessions.db"}.owners`);
  }

  get #sessionLockDirectory(): string {
    return join(dirname(this.databasePath), `.${this.databasePath.split(/[\\/]/).at(-1) ?? "sessions.db"}.sessions`);
  }

  #initializeDatabase(): void {
    this.#database.exec(`
      PRAGMA journal_mode = WAL;

      CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        provider_thread_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
        content TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'streaming', 'completed', 'failed', 'interrupted', 'incomplete')),
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        created_at TEXT NOT NULL
      );

      CREATE INDEX IF NOT EXISTS messages_session_created ON messages(session_id, created_at);

      CREATE TABLE IF NOT EXISTS requests (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        user_message_id TEXT NOT NULL REFERENCES messages(id),
        status TEXT NOT NULL CHECK (status IN ('pending', 'streaming', 'completed', 'failed', 'interrupted', 'incomplete')),
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        owner_id TEXT,
        started_at TEXT NOT NULL,
        completed_at TEXT
      );

      CREATE TABLE IF NOT EXISTS tool_traces (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        request_id TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL,
        call_id TEXT NOT NULL,
        name TEXT NOT NULL,
        arguments_json TEXT NOT NULL,
        result_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
        duration_ms INTEGER NOT NULL,
        created_at TEXT NOT NULL
      );

      CREATE INDEX IF NOT EXISTS tool_traces_request_sequence ON tool_traces(request_id, sequence);
    `);
    const columns = this.#database.prepare("PRAGMA table_info(requests)").all() as SqlRow[];
    if (!columns.some((row) => row.name === "owner_id")) {
      this.#database.exec("ALTER TABLE requests ADD COLUMN owner_id TEXT");
    }
  }

  #acquireOwnerLock(): void {
    mkdirSync(this.#ownerLockDirectory, { recursive: true, mode: 0o700 });
    const path = join(this.#ownerLockDirectory, `${this.ownerId}.lock`);
    this.#ownerLock = createOwnedLock(path, this.ownerId);
  }

  #recoverInterruptedProcesses(): void {
    const rows = this.#database.prepare(`
      SELECT DISTINCT owner_id FROM requests WHERE status IN ('pending', 'streaming')
    `).all() as SqlRow[];
    const staleOwnerIds: string[] = [];
    let recoverNullOwner = false;
    for (const row of rows) {
      const ownerId = row.owner_id;
      if (ownerId === null || ownerId === undefined) {
        recoverNullOwner = true;
      } else if (typeof ownerId === "string" && ownerId !== this.ownerId) {
        const path = join(this.#ownerLockDirectory, `${ownerId}.lock`);
        if (!isLockProcessAlive(path)) staleOwnerIds.push(ownerId);
      }
    }
    const conditions: string[] = [];
    const parameters: string[] = [];
    if (recoverNullOwner) conditions.push("owner_id IS NULL");
    if (staleOwnerIds.length > 0) {
      conditions.push(`owner_id IN (${staleOwnerIds.map(() => "?").join(", ")})`);
      parameters.push(...staleOwnerIds);
    }
    if (conditions.length === 0) return;
    const ownerFilter = conditions.join(" OR ");
    const sessionRows = this.#database.prepare(`
      SELECT DISTINCT session_id
      FROM requests
      WHERE status IN ('pending', 'streaming') AND (${ownerFilter})
    `).all(...parameters) as SqlRow[];
    const sessionIds = sessionRows.map((row) => asString(row.session_id));
    if (sessionIds.length === 0) return;

    this.#transaction(() => {
      this.#database.prepare(`
        UPDATE messages
        SET status = 'incomplete'
        WHERE id IN (
          SELECT user_message_id FROM requests
          WHERE status IN ('pending', 'streaming') AND (${ownerFilter})
        )
      `).run(...parameters);
      this.#database.prepare(`
        UPDATE requests
        SET status = 'incomplete',
            error = COALESCE(error, '上次 CLI 在请求完成前退出'),
            completed_at = ?
        WHERE status IN ('pending', 'streaming') AND (${ownerFilter})
      `).run(nowShanghai(), ...parameters);
      this.#database.prepare(`
        UPDATE sessions SET provider_thread_id = NULL, updated_at = ?
        WHERE id IN (${sessionIds.map(() => "?").join(", ")})
      `).run(nowShanghai(), ...sessionIds);
    });
  }

  #transaction<T>(operation: () => T): T {
    this.#database.exec("BEGIN IMMEDIATE");
    try {
      const result = operation();
      this.#database.exec("COMMIT");
      return result;
    } catch (error) {
      this.#database.exec("ROLLBACK");
      throw error;
    }
  }

  #releaseLocks(): void {
    for (const lock of this.#sessionLocks.values()) releaseOwnedLock(lock);
    this.#sessionLocks.clear();
    if (this.#ownerLock !== undefined) {
      releaseOwnedLock(this.#ownerLock);
      this.#ownerLock = undefined;
    }
  }

  #assertOpen(): void {
    if (this.#closed) throw new Error("Session Store 已关闭");
  }
}

function messageFromRow(row: SqlRow): Message {
  return {
    id: asString(row.id),
    sessionId: asString(row.session_id),
    role: asString(row.role) as MessageRole,
    content: asString(row.content),
    status: asString(row.status) as MessageStatus,
    provider: asString(row.provider),
    model: asString(row.model),
    createdAt: asString(row.created_at),
  };
}

function sessionFromRow(row: SqlRow): PurchaseSession {
  const base = {
    id: asString(row.id),
    provider: asString(row.provider),
    model: asString(row.model),
    createdAt: asString(row.created_at),
    updatedAt: asString(row.updated_at),
  };
  return row.provider_thread_id === null || row.provider_thread_id === undefined
    ? base
    : { ...base, providerThreadId: asString(row.provider_thread_id) };
}

function createOwnedLock(path: string, ownerId: string): OwnedLock {
  const descriptor = openSync(path, "wx", 0o600);
  try {
    writeFileSync(descriptor, JSON.stringify({ pid: process.pid, ownerId, startedAt: Date.now() }));
    return { path, descriptor };
  } catch (error) {
    closeSync(descriptor);
    rmSync(path, { force: true });
    throw error;
  }
}

function releaseOwnedLock(lock: OwnedLock): void {
  try {
    closeSync(lock.descriptor);
  } catch {
    // The descriptor may already have been closed during process teardown.
  }
  rmSync(lock.path, { force: true });
}

function isLockProcessAlive(path: string): boolean {
  if (!existsSync(path)) return false;
  let payload: unknown;
  try {
    payload = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return false;
  }
  if (typeof payload !== "object" || payload === null || !("pid" in payload)) return false;
  const pid = Number((payload as { pid: unknown }).pid);
  if (!Number.isSafeInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
}

function isAlreadyExists(error: unknown): boolean {
  return (error as NodeJS.ErrnoException)?.code === "EEXIST";
}

function asString(value: unknown): string {
  if (typeof value !== "string") throw new Error("SQLite 行包含无效字符串字段");
  return value;
}

function compactUuid(): string {
  return randomUUID().replaceAll("-", "");
}

function nowShanghai(): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date());
  const get = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((part) => part.type === type)?.value ?? "00";
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}:${get("second")}+08:00`;
}
