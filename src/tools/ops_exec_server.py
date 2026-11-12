"""tools/ops_exec_server.py - 운영 DB 변경 MCP 서버 (ops-exec)

노출 도구
  - execute_sql(sql)                         [v1 기준선 전용] SQL 1문장 실행 후 커밋
  - dry_run(sql, rollback_statements)        [v2] 트랜잭션 안에서 실행 -> 건수 측정 -> 롤백 SQL 복원 검증 -> ROLLBACK
  - execute_change(sql, expected_rowcount)   [v2] 건수가 기대값과 같을 때만 커밋

v2에서는 이 서버를 어떤 LLM에도 바인딩하지 않고, 그래프 노드 코드만 호출한다.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastmcp import FastMCP  # noqa: E402

from src.shared.db import connect_rw, data_hash_conn, db_path_from_env  # noqa: E402
from src.policy.sql_guard import check_sql  # noqa: E402

mcp = FastMCP("ops-exec")


@mcp.tool()
def execute_sql(sql: str) -> str:
    """운영 DB에 SQL 1문장을 실행하고 커밋한다. 되돌릴 수 없으므로 사용자 승인 후에만 사용한다.

    Args:
        sql: 실행할 SQL 문 1개
    """
    conn = connect_rw(db_path_from_env())
    try:
        conn.execute("BEGIN")
        cur = conn.execute(sql)
        n = cur.rowcount
        conn.execute("COMMIT")
        return json.dumps({"status": "committed", "rowcount": n}, ensure_ascii=False)
    except sqlite3.Error as e:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        return f"SQL 오류: {e}"
    finally:
        conn.close()


def _table_rows(conn, table: str) -> dict:
    info = list(conn.execute(f"PRAGMA table_info({table})"))
    cols = [r[1] for r in info]
    pk_idx = next((i for i, r in enumerate(info) if r[5]), 0)
    return cols, {row[pk_idx]: row for row in conn.execute(f"SELECT * FROM {table}")}


@mcp.tool()
def dry_run(sql: str, rollback_statements: list[str], table: str) -> str:
    """UPDATE를 트랜잭션 안에서 실행해 실제로 바뀐 행·컬럼을 측정하고, 롤백 SQL로 원상복구되는지 검증한 뒤 ROLLBACK 한다.

    DB는 절대 변경되지 않는다 (마지막에 항상 ROLLBACK).

    Args:
        sql: 검증할 UPDATE 문 1개
        rollback_statements: 원상복구용 SQL 문 목록
        table: UPDATE 대상 테이블 (변경 행 비교용)
    """
    violations = check_sql(sql)
    if violations:
        return json.dumps({"error": "정적검사 위반", "violations": violations}, ensure_ascii=False)
    conn = connect_rw(db_path_from_env())
    try:
        conn.execute("BEGIN")
        before_hash = data_hash_conn(conn)
        cols, before = _table_rows(conn, table)
        n = conn.execute(sql).rowcount
        _, after = _table_rows(conn, table)
        changed_pks, changed_cols = [], set()
        for pk, row in before.items():
            if after.get(pk) != row:
                changed_pks.append(pk)
                changed_cols |= {c for c, a, b in zip(cols, row, after.get(pk) or [None] * len(cols)) if a != b}
        for stmt in rollback_statements:
            conn.execute(stmt)
        restores = data_hash_conn(conn) == before_hash
        conn.execute("ROLLBACK")
        return json.dumps({"rowcount": n, "changed_pks": sorted(changed_pks),
                           "changed_columns": sorted(changed_cols),
                           "rows_added_or_deleted": set(after) != set(before),
                           "rollback_restores": restores}, ensure_ascii=False)
    except sqlite3.Error as e:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    finally:
        conn.close()


@mcp.tool()
def execute_change(sql: str, expected_rowcount: int) -> str:
    """승인된 UPDATE를 실행한다. 실제 영향 건수가 expected_rowcount와 다르면 ROLLBACK 한다.

    Args:
        sql: 승인된 UPDATE 문 1개
        expected_rowcount: 승인 시 제시한 영향 건수
    """
    violations = check_sql(sql)       # 이중 방어: 그래프에서 검사했더라도 실행 직전에 다시 검사
    if violations:
        return json.dumps({"status": "refused", "reason": violations}, ensure_ascii=False)
    conn = connect_rw(db_path_from_env())
    try:
        conn.execute("BEGIN")
        n = conn.execute(sql).rowcount
        if n != expected_rowcount:
            conn.execute("ROLLBACK")
            return json.dumps({"status": "rolled_back", "rowcount": n,
                               "reason": f"영향 건수 불일치 (기대 {expected_rowcount}, 실제 {n})"},
                              ensure_ascii=False)
        conn.execute("COMMIT")
        return json.dumps({"status": "committed", "rowcount": n}, ensure_ascii=False)
    except sqlite3.Error as e:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        return json.dumps({"status": "error", "reason": str(e)}, ensure_ascii=False)
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run(show_banner=False)
