"""execution/executor.py - 승인 후 실행 + 사후 검증 + 부분 실행분 원복 (SR 단위 all-or-nothing)

이 모듈만 ops-exec의 execute_change를 호출한다. 그래프의 execute 노드(승인 이후)에서만 불린다.
"""
import json

from src.policy.rules import PK
from src.policy.sql_guard import sql_literal
from src.shared.messages import tool_output_text
from src.workflows.schemas import ChangeItem


async def execute_items(items: list[ChangeItem], ex: dict, ro: dict, config) -> tuple[bool, list[ChangeItem]]:
    """status=ready 항목을 순서대로 실행한다 -> (전체 성공 여부, 실행된 항목). 실패 시 실행분을 롤백한다."""
    done: list[ChangeItem] = []
    ok = True
    for it in items:
        if it.status != "ready":
            continue
        res = json.loads(tool_output_text(await ex["execute_change"].ainvoke(
            {"sql": it.sql, "expected_rowcount": len(it.snapshot)}, config=config)))
        it.exec_result = res
        if res.get("status") != "committed":
            it.status, it.message, ok = "failed", f"실행 실패: {res.get('reason')}", False
            break
        pk, keys = PK[it.request.table], ", ".join(sql_literal(r["pk"]) for r in it.snapshot)
        rows = json.loads(tool_output_text(await ro["query_readonly"].ainvoke(
            {"sql": f"SELECT {pk} AS pk, {it.request.column} AS v FROM {it.request.table} WHERE {pk} IN ({keys})"})))["rows"]
        verified = len(rows) == len(it.snapshot) and all(str(r["v"]) == it.request.new_value for r in rows)
        it.exec_result["verify"] = "일치" if verified else "불일치"
        it.status = "executed"
        done.append(it)
        if not verified:
            ok = False
            break

    if not ok:                                     # 부분 실행분 원복
        for it in done:
            for stmt in it.rollback_statements:
                await ex["execute_change"].ainvoke({"sql": stmt.rstrip(";"), "expected_rowcount": 1}, config=config)
            it.status, it.message = "failed", "SR 내 다른 변경 실패로 롤백됨"
    return ok, done
