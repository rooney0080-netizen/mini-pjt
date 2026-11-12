"""reporting/templates.py - 승인 카드와 처리 리포트의 고정 문구 템플릿 + 출력 가드

사용자에게 보이는 텍스트는 LLM이 아니라 이 템플릿이 만든다. 내보내기 전에 guard_output으로 PII를 마스킹한다.
"""
from src.shared.pii import mask_pii
from src.workflows.schemas import BLOCKING, ChangeItem, SRState


def guard_output(text: str) -> str:
    """출력 가드: 사용자에게 보이는 모든 텍스트의 PII 마스킹"""
    return mask_pii(text)


def msg_for(item: ChangeItem) -> str:
    cr = item.request
    return {
        "not_found": f"대상 없음: '{cr.target_desc}'에 해당하는 {cr.table} 레코드가 존재하지 않습니다. 키를 확인해 다시 요청해 주세요.",
        "ambiguous": (f"대상 특정 불가: '{cr.target_desc}'에 해당하는 후보가 여러 건입니다 "
                      f"({', '.join(item.candidates)}). 고유 키(회원번호 등)로 다시 요청해 주세요."),
        "no_change": f"변경 불필요: 대상이 이미 '{cr.new_value}' 상태입니다.",
        "ready": (f"검증 통과 (대상 {', '.join(r['pk'] for r in item.snapshot)}, {len(item.snapshot)}건) "
                  "- 실행하지 않음 (승인 거절 또는 SR 내 다른 항목 차단). 변경 0건."),
    }.get(item.status, item.message)


def render_card(items: list[ChangeItem]) -> str:
    ready = [i for i in items if i.status == "ready"]
    lines = [f"## 변경 승인 요청 (실행 대상 {len(ready)}건 / 하위 변경 {len(items)}건)"]
    for it in items:
        cr = it.request
        lines.append(f"\n### [{it.index}] {cr.table}.{cr.column} -> '{cr.new_value}'  ({it.status})")
        if it.status != "ready":
            lines.append(f"- {msg_for(it)}")
            continue
        lines.append(f"- 사유: {cr.reason or '미기재'}")
        lines.append(f"- 영향 건수: {len(it.snapshot)}건 (dry-run 실측 {it.dry_run.get('rowcount')}건, "
                     f"롤백 복원 검증 {'통과' if it.dry_run.get('rollback_restores') else '실패'})")
        lines.append("\n| 키 | 변경 전 | 변경 후 |\n|---|---|---|")
        lines += [f"| {r['pk']} | {r['old']} | {cr.new_value} |" for r in it.snapshot]
        lines.append(f"\n실행 SQL:\n```sql\n{it.sql};\n```")
        lines.append("롤백 SQL:\n```sql\n" + "\n".join(it.rollback_statements) + "\n```")
        if it.review:
            lines.append(f"- Reviewer: {it.review}")
        lines += [f"- ⚠ {w}" for w in it.warnings]
    lines.append("\n승인(approve) 또는 거절(reject)을 선택하세요.")
    return "\n".join(lines)


def render_report(state: SRState, items: list[ChangeItem]) -> str:
    if state.get("blocked_message"):
        return f"## 처리 결과: 차단\n{state['blocked_message']}"
    if not items:
        return "## 처리 결과: 변경 없음\n요청에서 변경 작업을 찾지 못했습니다. 변경 대상·항목·값을 명시해 다시 요청해 주세요. 변경 0건."
    decision = state.get("decision")
    stopped = [i for i in items if i.status in BLOCKING]
    if stopped:
        head = "## 처리 결과: 차단 (SR 전체 미실행, 변경 0건)\n하위 변경 중 처리할 수 없는 항목이 있어 SR 전체를 실행하지 않았습니다."
    elif all(i.status == "no_change" for i in items):
        head = "## 처리 결과: 변경 불필요 (변경 0건)"
    elif decision and decision != "approve":
        head = "## 처리 결과: 승인 거절 (변경 0건)\n운영자가 승인을 거절해 실행하지 않았습니다. 롤백 불필요."
    elif state.get("executed"):
        n = sum(len(i.snapshot) for i in items if i.status == "executed")
        head = f"## 처리 결과: 실행 완료 (총 {n}건 변경, 사후 검증 완료, 변경 이력 저장)"
    else:
        head = "## 처리 결과: 실행 실패 (변경 0건, 실행분은 롤백됨)"
    lines = [head]
    for it in items:
        cr = it.request
        lines.append(f"\n### [{it.index}] {cr.target_desc}: {cr.table}.{cr.column} -> '{cr.new_value}'")
        if it.status == "executed":
            lines.append(f"- 변경 {len(it.snapshot)}건: " + ", ".join(f"{r['pk']} '{r['old']}' -> '{cr.new_value}'" for r in it.snapshot))
            lines.append(f"- 사유: {cr.reason or '미기재'} / 사후 검증: {it.exec_result.get('verify')}")
            lines.append("- 롤백 SQL:\n```sql\n" + "\n".join(it.rollback_statements) + "\n```")
        else:
            lines.append(f"- {msg_for(it)}")
        lines += [f"- ⚠ {w}" for w in it.warnings]
    return "\n".join(lines)
