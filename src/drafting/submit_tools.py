"""drafting/submit_tools.py - 하위 Agent가 결과를 넘기는 '제출 도구'

Agent는 결과를 말로 보고하지 않고 이 도구로 workspace(ws)에 기록한다. 도구 안에서 코드가 즉시 재검증하므로
(조건 재실행·건수 한도·명시 키 일치·sqlglot 정적검사·SET 값 일치) 코드가 검증한 값만 다음 단계로 넘어간다.
"""
import json
from typing import Literal

from langchain_core.tools import tool

from src.intake.schemas import ChangeRequest
from src.policy.rules import MAX_AFFECTED, PK
from src.policy.sql_guard import check_sql, set_values
from src.shared.messages import tool_output_text


def make_submit_tools(cr: ChangeRequest, ws: dict, ro: dict) -> dict:
    """하위 변경 1건 전용 제출 도구 4종 -> {name: tool}. ro: ops-readonly 도구 dict"""
    table, column, pk = cr.table, cr.column, PK[cr.table]

    @tool
    async def submit_targets(status: Literal["found", "not_found", "ambiguous"], where_clause: str = "",
                             candidates: list[str] | None = None, note: str = "") -> str:
        """대상 분석 결과를 제출한다. 코드가 조건을 다시 실행해 건수·대상을 검증한다.

        Args:
            status: found(대상 확정) / not_found(대상 없음) / ambiguous(후보가 여러 건이라 특정 불가)
            where_clause: found일 때 대상을 고르는 WHERE 조건식 (WHERE 키워드 제외)
            candidates: ambiguous일 때 후보 PK 목록
            note: 판단 근거 한 문장
        """
        if status == "not_found":
            ws["analysis"] = {"status": "not_found", "note": note}
            return "제출 완료(대상 없음). 작업을 종료한다."
        if status == "ambiguous":
            ws["analysis"] = {"status": "ambiguous", "candidates": candidates or [], "note": note}
            return "제출 완료(대상 특정 불가). 작업을 종료한다."

        raw = tool_output_text(await ro["count_affected"].ainvoke({"table": table, "where": where_clause}))
        try:
            count = json.loads(raw)["count"]
        except (ValueError, KeyError):
            return f"WHERE 조건 오류: {raw[:200]}. 수정해 다시 제출하라."
        if count == 0:
            ws["analysis"] = {"status": "not_found", "where": where_clause, "note": note}
            return "검증 결과 0건 -> 대상 없음으로 처리. 작업을 종료한다."
        if count > MAX_AFFECTED:
            ws["analysis"] = {"status": "over_limit", "where": where_clause, "count": count}
            return f"검증 결과 {count}건 -> 1회 한도 {MAX_AFFECTED}건 초과로 차단. 작업을 종료한다."

        raw = tool_output_text(await ro["query_readonly"].ainvoke(
            {"sql": f"SELECT {pk} AS pk, {column} AS old FROM {table} WHERE {where_clause} ORDER BY {pk}"}))
        rows = json.loads(raw)["rows"]
        pks = [r["pk"] for r in rows]
        if cr.key_values and set(pks) != set(cr.key_values):
            return (f"검증 실패: 요청에 명시된 키 {cr.key_values}와 조건 결과 {pks}가 다르다. "
                    f"명시된 키만 고르는 조건으로 수정해 다시 제출하라. 명시된 키가 DB에 없으면 not_found로 제출하라.")
        to_change = [r for r in rows if str(r["old"]) != cr.new_value]
        ws["analysis"] = {"status": "found", "where": where_clause, "snapshot": rows, "to_change": to_change}
        if not to_change:
            return f"검증 통과: 대상 {pks} 모두 이미 '{cr.new_value}' -> 변경 불필요. 작업을 종료한다."
        return json.dumps({"검증": "통과", "변경 필요 키": [r["pk"] for r in to_change],
                           "이미 목표값인 키": [r["pk"] for r in rows if r not in to_change],
                           "다음 단계": f"sql_agent가 {table}.{column}='{cr.new_value}' WHERE {pk} IN (변경 필요 키)로 작성"},
                          ensure_ascii=False)

    @tool
    def submit_sql(sql: str) -> str:
        """작성한 UPDATE SQL 1문장을 제출한다. 코드가 정적검사(UPDATE 1문장·WHERE·화이트리스트·요청 컬럼/값)를 수행한다.

        Args:
            sql: UPDATE 문 1개
        """
        violations = check_sql(sql, expect_table=table, expect_columns={column})
        if not violations:
            value = set_values(sql).get(column)
            if value != cr.new_value:
                violations.append(f"SET 값 '{value}'이(가) 요청 값 '{cr.new_value}'과 다름")
        ws["sql"], ws["sql_violations"] = sql.strip().rstrip(";"), violations
        ws["sql_attempts"] = ws.get("sql_attempts", 0) + 1
        if violations:
            return f"정적검사 위반: {violations}. 수정해 다시 제출하라."
        return "정적검사 통과. 제출 완료."

    @tool
    def get_review_packet() -> str:
        """검토에 필요한 요청 내용·검증된 대상·제출 SQL·정적검사 결과를 반환한다."""
        a = ws.get("analysis", {})
        return json.dumps({
            "request": cr.model_dump(),
            "verified_targets_to_change": [r["pk"] for r in a.get("to_change", [])],
            "sql": ws.get("sql"), "static_check_violations": ws.get("sql_violations"),
        }, ensure_ascii=False)

    @tool
    def submit_review(approved: bool, comment: str) -> str:
        """검토 결과를 제출한다.

        Args:
            approved: SQL이 요청 의도와 정확히 일치하면 true, 아니면 false
            comment: 판단 근거 또는 반려 사유
        """
        ws["review"] = {"approved": approved, "comment": comment, "sql": ws.get("sql")}
        return "검토 결과 제출 완료."

    return {t.name: t for t in (submit_targets, submit_sql, get_review_packet, submit_review)}
