"""shared/db.py - SQLite 접속·해시 유틸 (MCP 서버와 평가 러너 공용)"""
import hashlib
import os
import sqlite3
from pathlib import Path


def db_path_from_env() -> str:
    path = os.environ.get("OPS_DB_PATH")
    if not path:
        raise RuntimeError("OPS_DB_PATH 환경변수가 없습니다 (MCP 클라이언트가 넘겨줘야 함)")
    return path


def connect_ro(path: str) -> sqlite3.Connection:
    """읽기 전용 연결: 어떤 SQL이 와도 DB를 바꿀 수 없다."""
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def connect_rw(path: str) -> sqlite3.Connection:
    """트랜잭션을 직접 제어하기 위해 autocommit 모드(isolation_level=None)로 연다."""
    return sqlite3.connect(path, isolation_level=None)


def schema_hash_conn(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16]


def data_hash_conn(conn: sqlite3.Connection) -> str:
    h = hashlib.sha256()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        h.update(t.encode())
        for row in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2"):
            h.update(repr(row).encode())
    return h.hexdigest()[:16]


def schema_hash(path) -> str:
    conn = sqlite3.connect(path)
    try:
        return schema_hash_conn(conn)
    finally:
        conn.close()


def data_hash(path) -> str:
    conn = sqlite3.connect(path)
    try:
        return data_hash_conn(conn)
    finally:
        conn.close()
