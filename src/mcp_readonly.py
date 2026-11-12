"""mcp_readonly.py - 운영 DB 조회 전용 MCP 서버 (ops-readonly)

노출 도구 (모두 읽기 전용 연결 mode=ro 사용 -> 어떤 SQL이 와도 DB 변경 불가)
  - describe_table(table)            컬럼, PK, 허용 코드값
  - query_readonly(sql)              SELECT 실행 (최대 50행)
  - count_affected(table, where)     WHERE 조건에 해당하는 행 수

DB 경로는 클라이언트가 환경변수 OPS_DB_PATH 로 넘긴다.
실행: python src/mcp_readonly.py (stdio, 클라이언트가 자식 프로세스로 띄움)
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastmcp import FastMCP  # noqa: E402

from src.db import connect_ro, db_path_from_env  # noqa: E402

mcp = FastMCP("ops-readonly")
MAX_ROWS = 50


def _tables(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


@mcp.tool()
def describe_table(table: str) -> str:
    """운영 DB 테이블의 컬럼 목록, PK, 컬럼별 허용 코드값을 조회한다.

    변경 요청을 처리하기 전에 대상 테이블 구조와 허용 값을 확인할 때 사용한다.

    Args:
        table: 테이블 이름 (members, orders, payments)
    """
    conn = connect_ro(db_path_from_env())
    try:
        tables = _tables(conn)
        if table not in tables:
            return f"'{table}' 테이블이 없습니다. 존재하는 테이블: {tables}"
        cols = [{"name": r[1], "type": r[2], "pk": bool(r[5])}
                for r in conn.execute(f"PRAGMA table_info({table})")]
        codes: dict[str, list[str]] = {}
        for col, val in conn.execute(
                "SELECT column_name, value FROM code_values WHERE table_name=? ORDER BY rowid", (table,)):
            codes.setdefault(col, []).append(val)
        return json.dumps({"table": table, "columns": cols, "allowed_values": codes}, ensure_ascii=False)
    finally:
        conn.close()


@mcp.tool()
def query_readonly(sql: str) -> str:
    """SELECT 문을 실행해 결과 행을 JSON으로 반환한다 (최대 50행).

    변경 대상 레코드의 현재 값을 확인할 때 사용한다. 데이터 변경은 불가능하다.

    Args:
        sql: 실행할 SELECT 문 1개
    """
    conn = connect_ro(db_path_from_env())
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql)
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        data = [dict(r) for r in rows[:MAX_ROWS]]
        return json.dumps({"row_count": len(data), "truncated": truncated, "rows": data}, ensure_ascii=False)
    except sqlite3.Error as e:
        return f"SQL 오류: {e}. 읽기 전용 SELECT 문 1개만 실행할 수 있습니다."
    finally:
        conn.close()


@mcp.tool()
def count_affected(table: str, where: str) -> str:
    """WHERE 조건에 해당하는 행 수를 센다. UPDATE 전에 영향 건수를 확인할 때 사용한다.

    Args:
        table: 테이블 이름
        where: WHERE 키워드를 뺀 조건식 (예: "order_id = 'ORD-20231'")
    """
    conn = connect_ro(db_path_from_env())
    try:
        if table not in _tables(conn):
            return f"'{table}' 테이블이 없습니다."
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
        return json.dumps({"table": table, "where": where, "count": n}, ensure_ascii=False)
    except sqlite3.Error as e:
        return f"SQL 오류: {e}"
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run(show_banner=False)
