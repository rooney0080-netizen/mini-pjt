"""guards.py - 입력 가드 / SQL 정적검사 가드 / 출력 가드 (모두 코드로 강제, LLM 판단에 의존하지 않음)"""
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from src.config import WRITABLE
from src.pii import mask_pii

# ---------------- 입력 가드 ----------------
INPUT_RULES = [
    ("SQL 인젝션 구문", re.compile(r";\s*(drop|delete|update|insert|alter|truncate|create|exec)\b", re.I)),
    ("SQL 주석 기호", re.compile(r"(--|/\*|\*/)")),
    ("SQL 항진식", re.compile(r"'\s*or\s*'?\w*'?\s*=\s*'?\w*", re.I)),
    ("UNION SELECT 구문", re.compile(r"\bunion\s+(all\s+)?select\b", re.I)),
    ("DDL 구문", re.compile(r"\b(drop|truncate|alter)\s+table\b", re.I)),
    ("지시 무시 요구", re.compile(r"(이전|앞의|위의|기존|모든)\s*(지시|명령|규칙|지침)[^.\n]{0,10}(무시|잊)")),
    ("지시 무시 요구", re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules)", re.I)),
    ("승인 절차 우회 요구", re.compile(r"승인\s*(절차|단계|과정)?\s*(없이|생략|건너|패스|무시)")),
    ("WHERE 조건 생략 요구", re.compile(r"where\s*(조건|절)?\s*(없이|빼고|생략)", re.I)),
    ("처리 결과 허위 보고 요구", re.compile(r"(보고서?|리포트|결과)[^.\n]{0,10}(\d+\s*건)?[^.\n]{0,5}(이?라고|로)\s*(쓰|적|기재|보고)")),
]


@dataclass
class InputVerdict:
    blocked: bool
    rule: str = ""
    message: str = ""


def check_input(text: str) -> InputVerdict:
    for name, pattern in INPUT_RULES:
        if pattern.search(text):
            return InputVerdict(True, name, (
                f"해당 요청은 처리할 수 없습니다: 요청 본문에 '{name}'이(가) 포함되어 있습니다. "
                "변경 대상(키)·변경할 항목·변경 값·사유만 담아 다시 요청해 주세요. 변경 0건."))
    return InputVerdict(False)


# ---------------- SQL 정적검사 가드 ----------------
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


# ---------------- 출력 가드 ----------------
def guard_output(text: str) -> str:
    """사용자에게 보이는 모든 텍스트의 PII 마스킹"""
    return mask_pii(text)
