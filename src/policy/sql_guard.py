"""policy/sql_guard.py - SQL 정적검사 가드 (sqlglot AST) + SQL 리터럴 유틸

check_sql: UPDATE 1문장, WHERE 필수(컬럼 참조), DDL·DELETE·INSERT·다중 문장 금지, 화이트리스트 테이블·컬럼만 허용.
sql_agent 제출 시, 그래프 코드 게이트, ops-exec 서버(dry_run·execute_change)에서 세 번 적용된다.
"""
import sqlglot
from sqlglot import exp

from src.policy.rules import WRITABLE

FORBIDDEN = (exp.Delete, exp.Insert, exp.Drop, exp.Alter, exp.Create, exp.Command, exp.TruncateTable)


def check_sql(sql: str, expect_table: str | None = None, expect_columns: set[str] | None = None) -> list[str]:
    """UPDATE 1문장, WHERE 필수, 화이트리스트 테이블·컬럼만 허용. 위반 사유 목록을 반환 (빈 리스트 = 통과)."""
    try:
        stmts = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError as e:
        return [f"SQL 파싱 실패: {str(e)[:120]}"]
    if len(stmts) != 1:
        return [f"다중 문장 금지 (문장 {len(stmts)}개)"]
    stmt = stmts[0]
    if isinstance(stmt, FORBIDDEN) or stmt.find(*FORBIDDEN):
        return [f"금지된 구문: {stmt.key.upper()} (UPDATE만 허용)"]
    if not isinstance(stmt, exp.Update):
        return [f"UPDATE만 허용 (입력: {stmt.key.upper()})"]

    violations = []
    table = stmt.this.name if isinstance(stmt.this, exp.Table) else str(stmt.this)
    if table not in WRITABLE:
        violations.append(f"변경 불가 테이블: {table}")
    if expect_table and table != expect_table:
        violations.append(f"요청 테이블({expect_table})과 SQL 테이블({table}) 불일치")

    set_cols = {e.this.name for e in stmt.expressions if isinstance(e, exp.EQ) and isinstance(e.this, exp.Column)}
    if not set_cols:
        violations.append("SET 절을 해석할 수 없음")
    bad = set_cols - WRITABLE.get(table, set())
    if bad:
        violations.append(f"변경 불가 컬럼: {sorted(bad)}")
    if expect_columns is not None and set_cols != expect_columns:
        violations.append(f"요청 컬럼 {sorted(expect_columns)} 외 변경 시도: {sorted(set_cols)}")

    where = stmt.args.get("where")
    if where is None:
        violations.append("WHERE 없는 UPDATE 금지")
    elif not list(where.find_all(exp.Column)):
        violations.append("의미 없는 WHERE 조건 (컬럼 참조 없음, 예: 1=1)")
    return violations


def sql_literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def set_values(sql: str) -> dict:
    """UPDATE 문의 SET 컬럼 -> 리터럴 값"""
    stmt = sqlglot.parse_one(sql, read="sqlite")
    out = {}
    for e in stmt.expressions:
        if isinstance(e, exp.EQ) and isinstance(e.this, exp.Column):
            out[e.this.name] = e.expression.name if isinstance(e.expression, exp.Literal) else e.expression.sql()
    return out
