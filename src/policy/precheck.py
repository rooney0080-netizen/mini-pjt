"""policy/precheck.py - 하위 변경 1건의 정책 사전검사 (작업 종류, 화이트리스트, 허용 코드값)

LLM을 부르기 전에 코드로 판정한다. 통과하면 None, 위반이면 사용자에게 보일 차단 메시지를 반환한다.
"""
import json

from src.intake.schemas import ChangeRequest
from src.policy.rules import WRITABLE, WRITABLE_DESC
from src.shared.messages import tool_output_text


async def precheck(cr: ChangeRequest, ro: dict) -> str | None:
    """ro: ops-readonly 도구 dict (허용 코드값 조회용 describe_table)"""
    if cr.operation != "update":
        return (f"지원 범위 밖: {cr.operation.upper()} 요청은 처리할 수 없습니다. "
                "이 에이전트는 UPDATE 보정만 지원합니다. 삭제·추가는 DBA 정식 절차로 요청해 주세요.")
    if cr.table not in WRITABLE or cr.column not in WRITABLE[cr.table]:
        return f"변경 불가 항목: {cr.table}.{cr.column}은(는) 변경 허용 대상이 아닙니다. 허용 항목: {WRITABLE_DESC}"
    desc = json.loads(tool_output_text(await ro["describe_table"].ainvoke({"table": cr.table})))
    allowed_vals = desc["allowed_values"].get(cr.column, [])
    if cr.new_value not in allowed_vals:
        return (f"허용되지 않은 값: {cr.table}.{cr.column}에 '{cr.new_value}'은(는) 허용 코드값이 아닙니다. "
                f"허용값: {allowed_vals}. 올바른 값으로 다시 요청해 주세요.")
    return None
