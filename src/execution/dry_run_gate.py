"""execution/dry_run_gate.py - 롤백 SQL 결정적 생성 + dry-run 검증 (ops-exec MCP의 dry_run 호출)

롤백 SQL은 LLM이 아니라 변경 전 스냅샷에서 코드가 만든다. dry-run 트랜잭션 안에서
실측 영향 행·컬럼이 검증 대상과 같은지, 롤백 SQL이 원상복구하는지 확인한 뒤 ROLLBACK 한다.
"""
import json

from src.intake.schemas import ChangeRequest
from src.policy.rules import PK
from src.policy.sql_guard import sql_literal
from src.shared.messages import tool_output_text
from src.workflows.schemas import ChangeItem


def rollback_statements(cr: ChangeRequest, rows: list[dict]) -> list[str]:
    pk = PK[cr.table]
    return [f"UPDATE {cr.table} SET {cr.column} = {sql_literal(r['old'])} WHERE {pk} = {sql_literal(r['pk'])};"
            for r in rows]


async def verify_with_dry_run(item: ChangeItem, ex: dict, config) -> list[str]:
    """item.sql을 dry-run 하고 item.rollback_statements / item.dry_run을 채운다. 문제 목록 반환 (빈 리스트 = 통과)"""
    cr = item.request
    item.rollback_statements = rollback_statements(cr, item.snapshot)
    dr = json.loads(tool_output_text(await ex["dry_run"].ainvoke(
        {"sql": item.sql, "rollback_statements": item.rollback_statements, "table": cr.table}, config=config)))
    item.dry_run = dr
    expected = sorted(r["pk"] for r in item.snapshot)
    problems = []
    if "error" in dr:
        problems.append(f"dry-run 오류: {dr.get('error')} {dr.get('violations', '')}")
    else:
        if dr["rowcount"] != len(expected) or dr["changed_pks"] != expected:
            problems.append(f"영향 행 불일치 (검증 대상 {expected}, dry-run {dr['changed_pks']})")
        if dr["changed_columns"] != [cr.column]:
            problems.append(f"요청 외 컬럼 변경 {dr['changed_columns']}")
        if dr["rows_added_or_deleted"]:
            problems.append("행 추가·삭제 발생")
        if not dr["rollback_restores"]:
            problems.append("롤백 SQL 복원 검증 실패")
    return problems
