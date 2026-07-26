"""SQLite 会话存储及固定事务边界。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import uuid4
from zoneinfo import ZoneInfo

from .models import (
    Message,
    MessageRole,
    MessageStatus,
    ProviderRuntime,
    PurchaseSession,
    TokenUsage,
    validate_1688_conversation_roles,
)


def _now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


class PurchaseSessionStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.owner_id = uuid4().hex
        self._owner_lock = None
        self._session_locks: dict[str, object] = {}
        database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._initialize_1688_purchase_database()
        self._acquire_1688_owner_lock()
        self._recover_1688_interrupted_processes()
        database_path.chmod(0o600)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize_1688_purchase_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
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
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'pending', 'streaming', 'completed',
                            'failed', 'interrupted', 'incomplete'
                        )
                    ),
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS messages_session_created
                ON messages(session_id, created_at);

                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    user_message_id TEXT NOT NULL REFERENCES messages(id),
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'pending', 'streaming', 'completed',
                            'failed', 'interrupted', 'incomplete'
                        )
                    ),
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
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(requests)"
                ).fetchall()
            }
            if "owner_id" not in columns:
                connection.execute(
                    "ALTER TABLE requests ADD COLUMN owner_id TEXT"
                )
                connection.commit()

    @property
    def _owner_lock_directory(self) -> Path:
        return self.database_path.parent / f".{self.database_path.name}.owners"

    @property
    def _session_lock_directory(self) -> Path:
        return self.database_path.parent / f".{self.database_path.name}.sessions"

    def _acquire_1688_owner_lock(self) -> None:
        self._owner_lock_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._owner_lock_directory / f"{self.owner_id}.lock"
        handle = path.open("a+", encoding="utf-8")
        path.chmod(0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self._owner_lock = handle

    def acquire_1688_session_lock(self, session_id: str) -> None:
        if session_id in self._session_locks:
            return
        self._session_lock_directory.mkdir(
            mode=0o700,
            parents=True,
            exist_ok=True,
        )
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        path = self._session_lock_directory / f"{digest}.lock"
        handle = path.open("a+", encoding="utf-8")
        path.chmod(0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError(
                f"Session 正在另一个 CLI 中使用：{session_id}"
            ) from exc
        self._session_locks[session_id] = handle

    def close(self) -> None:
        for handle in self._session_locks.values():
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        self._session_locks.clear()
        if self._owner_lock is not None:
            fcntl.flock(self._owner_lock.fileno(), fcntl.LOCK_UN)
            self._owner_lock.close()
            self._owner_lock = None

    def _recover_1688_interrupted_processes(self) -> None:
        """只恢复锁已释放的旧进程请求，不碰另一个活跃 CLI。"""

        with self._connect() as connection:
            owner_rows = connection.execute(
                """
                SELECT DISTINCT owner_id
                FROM requests
                WHERE status IN ('pending', 'streaming')
                """
            ).fetchall()
            if not owner_rows:
                return
            stale_owner_ids: list[str] = []
            recover_null_owner = False
            for row in owner_rows:
                owner_id = row["owner_id"]
                if owner_id is None:
                    recover_null_owner = True
                    continue
                if owner_id == self.owner_id:
                    continue
                path = self._owner_lock_directory / f"{owner_id}.lock"
                try:
                    handle = path.open("a+", encoding="utf-8")
                    fcntl.flock(
                        handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                except BlockingIOError:
                    handle.close()
                    continue
                except OSError:
                    stale_owner_ids.append(owner_id)
                    continue
                else:
                    stale_owner_ids.append(owner_id)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    handle.close()

            conditions: list[str] = []
            parameters: list[str] = []
            if recover_null_owner:
                conditions.append("owner_id IS NULL")
            if stale_owner_ids:
                placeholders = ", ".join("?" for _ in stale_owner_ids)
                conditions.append(f"owner_id IN ({placeholders})")
                parameters.extend(stale_owner_ids)
            if not conditions:
                return
            owner_filter = " OR ".join(conditions)
            session_rows = connection.execute(
                f"""
                SELECT DISTINCT session_id
                FROM requests
                WHERE status IN ('pending', 'streaming')
                  AND ({owner_filter})
                """,
                parameters,
            ).fetchall()
            session_ids = [row["session_id"] for row in session_rows]
            if not session_ids:
                return
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"""
                UPDATE messages
                SET status = 'incomplete'
                WHERE id IN (
                    SELECT user_message_id
                    FROM requests
                    WHERE status IN ('pending', 'streaming')
                      AND ({owner_filter})
                )
                """,
                parameters,
            )
            connection.execute(
                f"""
                UPDATE requests
                SET status = 'incomplete',
                    error = COALESCE(error, '上次 CLI 在请求完成前退出'),
                    completed_at = ?
                WHERE status IN ('pending', 'streaming')
                  AND ({owner_filter})
                """,
                [_now(), *parameters],
            )
            placeholders = ", ".join("?" for _ in session_ids)
            connection.execute(
                f"""
                UPDATE sessions
                SET provider_thread_id = NULL, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                (_now(), *session_ids),
            )
            connection.commit()

    def create_or_restore_1688_purchase_session(
        self,
        session_id: str | None,
        provider_runtime: ProviderRuntime,
    ) -> PurchaseSession:
        resolved_id = session_id or f"session_{uuid4().hex}"
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (resolved_id,),
            ).fetchone()
            if row is None:
                timestamp = _now()
                connection.execute(
                    """
                    INSERT INTO sessions(
                        id, provider, model, provider_thread_id, created_at, updated_at
                    ) VALUES (?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        resolved_id,
                        provider_runtime.provider,
                        provider_runtime.model,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.commit()
                row = connection.execute(
                    "SELECT * FROM sessions WHERE id = ?",
                    (resolved_id,),
                ).fetchone()
        assert row is not None
        return self._session_from_row(row)

    def get_1688_purchase_session(self, session_id: str) -> PurchaseSession:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Session 不存在：{session_id}")
        return self._session_from_row(row)

    def attach_1688_provider_thread(
        self,
        session_id: str,
        provider_thread_id: str,
        model: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET provider_thread_id = ?, model = ?, updated_at = ?
                WHERE id = ?
                """,
                (provider_thread_id, model, _now(), session_id),
            )
            connection.commit()

    def load_1688_purchase_context_messages(self, session_id: str) -> list[Message]:
        with self._connect() as connection:
            rows = connection.execute(
                """
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
                """,
                (session_id,),
            ).fetchall()
        messages = [self._message_from_row(row) for row in rows]
        validate_1688_conversation_roles(messages)
        return messages

    def begin_1688_purchase_request(
        self,
        session_id: str,
        user_input: str,
        provider_runtime: ProviderRuntime,
    ) -> tuple[Message, str]:
        user_message = Message(
            id=f"msg_{uuid4().hex}",
            session_id=session_id,
            role=MessageRole.USER,
            content=user_input,
            status=MessageStatus.COMPLETED,
            provider=provider_runtime.provider,
            model=provider_runtime.model,
            created_at=_now(),
        )
        request_id = f"request_{uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO messages(
                    id, session_id, role, content, status,
                    provider, model, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_message.id,
                    user_message.session_id,
                    user_message.role.value,
                    user_message.content,
                    user_message.status.value,
                    user_message.provider,
                    user_message.model,
                    user_message.created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO requests(
                    id, session_id, user_message_id, status,
                    provider, model, owner_id, started_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    request_id,
                    session_id,
                    user_message.id,
                    provider_runtime.provider,
                    provider_runtime.model,
                    self.owner_id,
                    _now(),
                ),
            )
            connection.commit()
        return user_message, request_id

    def mark_1688_purchase_request_streaming(self, request_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE requests SET status = 'streaming' WHERE id = ?",
                (request_id,),
            )
            connection.commit()

    def save_1688_purchase_reply(
        self,
        *,
        session_id: str,
        request_id: str,
        content: str,
        provider_runtime: ProviderRuntime,
        actual_model: str,
        usage: TokenUsage,
        provider_thread_id: str,
    ) -> Message:
        """Assistant、请求、用量和 Session 在同一事务中完成。"""

        assistant = Message(
            id=f"msg_{uuid4().hex}",
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            content=content,
            status=MessageStatus.COMPLETED,
            provider=provider_runtime.provider,
            model=actual_model,
            created_at=_now(),
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO messages(
                    id, session_id, role, content, status,
                    provider, model, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assistant.id,
                    assistant.session_id,
                    assistant.role.value,
                    assistant.content,
                    assistant.status.value,
                    assistant.provider,
                    assistant.model,
                    assistant.created_at,
                ),
            )
            connection.execute(
                """
                UPDATE requests
                SET status = 'completed',
                    model = ?,
                    input_tokens = ?,
                    output_tokens = ?,
                    total_tokens = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    actual_model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.total_tokens,
                    _now(),
                    request_id,
                ),
            )
            connection.execute(
                """
                UPDATE sessions
                SET model = ?, provider_thread_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (actual_model, provider_thread_id, _now(), session_id),
            )
            connection.commit()
        return assistant

    def fail_1688_purchase_request(
        self,
        *,
        request_id: str,
        user_message_id: str,
        status: MessageStatus,
        error: str,
    ) -> None:
        safe_error = error[:1_000]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE requests
                SET status = ?, error = ?, completed_at = ?
                WHERE id = ?
                """,
                (status.value, safe_error, _now(), request_id),
            )
            connection.execute(
                "UPDATE messages SET status = ? WHERE id = ?",
                (status.value, user_message_id),
            )
            connection.commit()

    def list_1688_purchase_sessions(self, limit: int = 20) -> list[PurchaseSession]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sessions
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            session_id=row["session_id"],
            role=MessageRole(row["role"]),
            content=row["content"],
            status=MessageStatus(row["status"]),
            provider=row["provider"],
            model=row["model"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> PurchaseSession:
        return PurchaseSession(
            id=row["id"],
            provider=row["provider"],
            model=row["model"],
            provider_thread_id=row["provider_thread_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
