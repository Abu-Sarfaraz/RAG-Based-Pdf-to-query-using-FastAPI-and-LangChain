"""Durable workspace, document, job and conversation catalog in SQLite."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
import uuid


class Catalog:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    content_hash TEXT NOT NULL, pipeline_version TEXT NOT NULL,
                    filename TEXT NOT NULL, size INTEGER NOT NULL, status TEXT NOT NULL,
                    pages INTEGER DEFAULT 0, chunks INTEGER DEFAULT 0, index_version TEXT,
                    error TEXT, preparation_seconds REAL,
                    suggestions TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(workspace_id, content_hash)
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    title TEXT NOT NULL, active_request TEXT, created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                    sources TEXT NOT NULL DEFAULT '[]', metrics TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'complete', created_at REAL NOT NULL,
                    UNIQUE(conversation_id, request_id, role)
                );
                CREATE INDEX IF NOT EXISTS document_jobs ON documents(status, created_at);
                CREATE INDEX IF NOT EXISTS conversation_owner ON conversations(workspace_id, updated_at);
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def row(value):
        if value is None:
            return None
        result = dict(value)
        for key in ("suggestions", "sources", "metrics"):
            if key in result:
                result[key] = json.loads(result[key])
        return result

    def create_workspace(self):
        token = secrets.token_urlsafe(32)
        workspace_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute("INSERT INTO workspaces VALUES (?, ?, ?)",
                               (workspace_id, hashlib.sha256(token.encode()).hexdigest(), time.time()))
        return {"id": workspace_id, "token": token}

    def authenticate(self, token: str):
        with self.connect() as connection:
            value = connection.execute("SELECT id FROM workspaces WHERE token_hash=?",
                                       (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            return value["id"] if value else None

    def register_document(self, owner, digest, pipeline, filename, size):
        now = time.time()
        document_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute(
                "SELECT * FROM documents WHERE workspace_id=? AND content_hash=?",
                (owner, digest)).fetchone()
            if old:
                return self.row(old), False
            connection.execute("""INSERT INTO documents
                (id, workspace_id, content_hash, pipeline_version, filename, size, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'uploading', ?, ?)""",
                (document_id, owner, digest, pipeline, filename, size, now, now))
            return self.row(connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()), True

    def document(self, owner, document_id):
        with self.connect() as connection:
            return self.row(connection.execute("SELECT * FROM documents WHERE id=? AND workspace_id=?",
                                               (document_id, owner)).fetchone())

    def documents(self, owner):
        with self.connect() as connection:
            return [self.row(row) for row in connection.execute(
                "SELECT * FROM documents WHERE workspace_id=? ORDER BY created_at DESC", (owner,))]

    def update_document(self, document_id, **values):
        allowed = {"status", "pages", "chunks", "index_version", "error", "preparation_seconds", "suggestions"}
        if not values.keys() <= allowed:
            raise ValueError("Unsupported document update")
        if "suggestions" in values:
            values["suggestions"] = json.dumps(values["suggestions"])
        values["updated_at"] = time.time()
        with self.connect() as connection:
            connection.execute("UPDATE documents SET " + ", ".join(key + "=?" for key in values) + " WHERE id=?",
                               (*values.values(), document_id))

    def claim_document(self):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM documents WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            connection.execute("UPDATE documents SET status='reading', error=NULL, updated_at=? WHERE id=?",
                               (time.time(), row["id"]))
            return self.row(row)

    def recover(self, pipeline_version=None):
        with self.connect() as connection:
            if pipeline_version:
                connection.execute("UPDATE documents SET status='queued',pipeline_version=?,error=NULL WHERE pipeline_version<>? AND status<>'uploading'",
                                   (pipeline_version, pipeline_version))
            connection.execute("UPDATE documents SET status='queued' WHERE status IN ('reading','preparing')")
            # A crash can leave an "uploading" row after the original PDF has
            # already been persisted. Requeue those rows; keep incomplete uploads
            # failed so their recovery action stays accurate.
            for row in connection.execute("SELECT id FROM documents WHERE status='uploading'").fetchall():
                original = self.path.parent / "uploads" / (row["id"] + ".pdf")
                if original.exists():
                    connection.execute("UPDATE documents SET status='queued', pipeline_version=COALESCE(?,pipeline_version), error=NULL, updated_at=? WHERE id=?",
                                       (pipeline_version, time.time(), row["id"]))
                else:
                    connection.execute("UPDATE documents SET status='failed', error='Upload interrupted. Please upload the PDF again.', updated_at=? WHERE id=?",
                                       (time.time(), row["id"]))
            interrupted = connection.execute("SELECT id,active_request FROM conversations WHERE active_request IS NOT NULL").fetchall()
            for conversation in interrupted:
                user = connection.execute("SELECT 1 FROM messages WHERE conversation_id=? AND request_id=? AND role='user'", tuple(conversation)).fetchone()
                if user:
                    connection.execute("""INSERT OR IGNORE INTO messages
                        (conversation_id,request_id,role,content,status,created_at) VALUES (?,?,'assistant',?,'failed',?)""",
                        (conversation["id"], conversation["active_request"], "The previous answer was interrupted. Please retry.", time.time()))
            connection.execute("UPDATE conversations SET active_request=NULL")

    def delete_document(self, owner, document_id):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            document = connection.execute("SELECT status FROM documents WHERE id=? AND workspace_id=?", (document_id, owner)).fetchone()
            active = connection.execute("SELECT 1 FROM conversations WHERE document_id=? AND active_request IS NOT NULL", (document_id,)).fetchone()
            if not document or document["status"] not in ("ready", "failed") or active:
                return False
            connection.execute("DELETE FROM documents WHERE id=? AND workspace_id=?", (document_id, owner))
            return True

    def create_conversation(self, owner, document_id):
        now = time.time()
        conversation_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute("INSERT INTO conversations (id,workspace_id,document_id,title,created_at,updated_at) VALUES (?,?,?,'New conversation',?,?)",
                               (conversation_id, owner, document_id, now, now))
        return self.conversation(owner, conversation_id)

    def conversation(self, owner, conversation_id):
        with self.connect() as connection:
            return self.row(connection.execute("SELECT * FROM conversations WHERE id=? AND workspace_id=?",
                                               (conversation_id, owner)).fetchone())

    def conversations(self, owner, *, limit=40, offset=0, search=""):
        with self.connect() as connection:
            return [self.row(row) for row in connection.execute("""SELECT c.*, d.filename FROM conversations c
                JOIN documents d ON d.id=c.document_id WHERE c.workspace_id=? AND (instr(lower(c.title),lower(?))>0 OR instr(lower(d.filename),lower(?))>0)
                ORDER BY c.updated_at DESC, c.id DESC LIMIT ? OFFSET ?""", (owner, search, search, limit, offset))]

    def messages(self, conversation_id):
        with self.connect() as connection:
            return [self.row(row) for row in connection.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,))]

    def acquire_conversation(self, conversation_id, request_id):
        with self.connect() as connection:
            cursor = connection.execute("UPDATE conversations SET active_request=? WHERE id=? AND active_request IS NULL",
                                        (request_id, conversation_id))
            return cursor.rowcount == 1

    def release_conversation(self, conversation_id, request_id):
        with self.connect() as connection:
            connection.execute("UPDATE conversations SET active_request=NULL WHERE id=? AND active_request=?", (conversation_id, request_id))

    def save_message(self, conversation_id, request_id, role, content, *, sources=None, metrics=None, status="complete"):
        now = time.time()
        with self.connect() as connection:
            connection.execute("""INSERT INTO messages
                (conversation_id,request_id,role,content,sources,metrics,status,created_at) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(conversation_id,request_id,role) DO UPDATE SET
                content=excluded.content,sources=excluded.sources,metrics=excluded.metrics,status=excluded.status""",
                (conversation_id, request_id, role, content, json.dumps(sources or []), json.dumps(metrics or {}), status, now))
            if role == "user":
                connection.execute("UPDATE conversations SET title=CASE WHEN title='New conversation' THEN ? ELSE title END,updated_at=? WHERE id=?",
                                   (content[:65], now, conversation_id))
            else:
                connection.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
