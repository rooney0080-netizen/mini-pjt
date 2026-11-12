"""history/store.py - 변경 이력 장기 메모리 (langgraph SqliteStore)

namespace: (HISTORY_NS, table, pk) / key: f"{column}@{iso시각}" / value: {column, old, new, reason, at, request}
평가 러너(F10 시드)도 같은 형식을 쓴다.
"""
import time

from src.intake.schemas import ChangeRequest
from src.shared.pii import mask_pii
from src.workflows.schemas import ChangeItem

HISTORY_NS = "sr_history"


def history_warnings(store, cr: ChangeRequest, pks: list[str]) -> list[str]:
    """같은 대상·같은 컬럼의 과거 변경이 있으면 승인 카드용 경고 문구를 만든다 (차단하지 않음)"""
    out = []
    for pk in pks:
        for it in store.search((HISTORY_NS, cr.table, pk), limit=20):
            v = it.value
            if v.get("column") == cr.column:
                out.append(f"동일 대상 변경 이력 있음 (중복 요청 주의): {pk} {v.get('column')} "
                           f"'{v.get('old')}' -> '{v.get('new')}' at {v.get('at')} (사유: {v.get('reason')})")
    return out


def record_changes(store, items: list[ChangeItem], request: str) -> None:
    """실행 완료된 항목의 행별 변경 전후 값을 저장한다"""
    at = time.strftime("%Y-%m-%dT%H:%M:%S")
    for it in items:
        for r in it.snapshot:
            store.put((HISTORY_NS, it.request.table, r["pk"]), f"{it.request.column}@{at}",
                      {"column": it.request.column, "old": r["old"], "new": it.request.new_value,
                       "reason": it.request.reason or "미기재", "at": at, "request": mask_pii(request)})
