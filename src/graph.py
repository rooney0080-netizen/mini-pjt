"""graph.py - v2: 고정 순서 StateGraph (Plan-Execute + Supervisor + HITL + Store)

  input_guard -> plan -> process(하위 변경마다: precheck -> Supervisor -> 코드 검증 -> dry-run)
              -> approval(interrupt) -> execute(코드) -> report
  차단·대상 없음·모호·한도 초과 등이 하나라도 있으면 SR 전체를 실행하지 않고 report로 간다 (all-or-nothing).

실행 도구(ops-exec)는 어떤 LLM에도 바인딩되지 않으며 execute 노드만 호출한다.
"""
import json
import os
import time
import uuid
from typing import TypedDict

from langchain_core.prompts import ChatPromptTemplate
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.store.sqlite import SqliteStore
from langgraph.types import Command, interrupt

from src.agents import build_change_supervisor, sql_literal, supervisor_input
from src.config import HISTORY_NS, MAX_AFFECTED, PK, WRITABLE, make_llm
from src.guards import check_input, check_sql, guard_output
from src.pii import mask_pii
from src.runtime import mcp_tools, tool_output_text
from src.schemas import ChangeItem, ChangePlan, ChangeRequest, ParsedSR
from src.tracer import FileTracer

BLOCKING = {"blocked", "not_found", "ambiguous", "over_limit", "failed"}

PLANNER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """너는 운영 DB 데이터 보정 요청(SR)을 하위 변경 작업 목록으로 분해하는 Planner다.

[스키마와 한국어 표현 매핑]
- members (PK member_id 'M-###'): name 이름, phone 전화번호, email 이메일, grade 등급, status 회원상태, joined_at 가입일
- orders (PK order_id 'ORD-#####'): member_id, order_date 주문일(YYYY-MM-DD), amount 결제금액/주문금액,
  order_status 주문상태, payment_status 결제상태, settle_status 정산상태, memo 메모
- payments (PK payment_id): order_id, method 결제수단, amount 결제금액, pg_status PG상태, paid_at 결제시각

[규칙]
- 요청에 명시된 변경 작업만 추출한다. 요청에 없는 작업을 추론해 추가하지 않는다.
- 변경 값은 요청 원문 그대로 쓴다. 잘못된 값처럼 보여도 교정하거나 비슷한 값으로 바꾸지 않는다.
- 삭제 요청은 operation=delete, 추가 요청은 insert로 표시한다.
- 같은 컬럼·같은 값으로 여러 키를 바꾸면 한 건으로 묶고 key_values에 모든 키를 넣는다.
- 서로 다른 컬럼·값·테이블의 변경은 각각 별도 항목으로 분리한다.
- 단순 조회·확인 요청은 변경 작업이 아니므로 추출하지 않는다.
- 사유가 적혀 있으면 reason에 원문 그대로 넣는다."""),
    ("human", "{request}"),
])


class SRState(TypedDict, total=False):
    request: str
    blocked_message: str
    items: list[dict]
    card: str
    decision: str
    executed: bool
    report: str


# ---------------- 메시지 템플릿 (고정 문구) ----------------
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


def rollback_statements(cr: ChangeRequest, rows: list[dict]) -> list[str]:
    pk = PK[cr.table]
    return [f"UPDATE {cr.table} SET {cr.column} = {sql_literal(r['old'])} WHERE {pk} = {sql_literal(r['pk'])};"
            for r in rows]


def history_warnings(store, cr: ChangeRequest, pks: list[str]) -> list[str]:
    out = []
    for pk in pks:
        for it in store.search((HISTORY_NS, cr.table, pk), limit=20):
            v = it.value
            if v.get("column") == cr.column:
                out.append(f"동일 대상 변경 이력 있음 (중복 요청 주의): {pk} {v.get('column')} "
                           f"'{v.get('old')}' -> '{v.get('new')}' at {v.get('at')} (사유: {v.get('reason')})")
    return out


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


# ---------------- 그래프 ----------------
def build_graph(servers: dict, store, tracer: FileTracer | None):
    ro, ex = servers["ops-readonly"], servers["ops-exec"]
    planner = PLANNER_PROMPT | make_llm().with_structured_output(ParsedSR)   # LCEL + Pydantic

    def log(event, **kw):
        if tracer:
            tracer.log(event, **kw)

    async def input_guard(state: SRState):
        if os.environ.get("SRFIXER_ABLATE_INPUT_GUARD") == "1":     # 평가용: 입력 가드 제거 실험
            log("input_guard", blocked=False, ablated=True)
            return {}
        v = check_input(state["request"])
        log("input_guard", blocked=v.blocked, rule=v.rule)
        return {"blocked_message": v.message} if v.blocked else {}

    async def plan(state: SRState, config):
        parsed: ParsedSR = await planner.ainvoke({"request": state["request"]}, config=config)
        items = [ChangeItem(index=i + 1, request=cr).model_dump() for i, cr in enumerate(parsed.changes)]
        log("plan", changes=[c.model_dump() for c in parsed.changes])
        return {"items": items}

    async def precheck(item: ChangeItem) -> bool:
        """코드 정책 검사: 작업 종류, 화이트리스트, 허용 코드값"""
        cr = item.request
        if cr.operation != "update":
            item.message = (f"지원 범위 밖: {cr.operation.upper()} 요청은 처리할 수 없습니다. "
                            "이 에이전트는 UPDATE 보정만 지원합니다. 삭제·추가는 DBA 정식 절차로 요청해 주세요.")
            return False
        if cr.table not in WRITABLE or cr.column not in WRITABLE[cr.table]:
            allowed = ", ".join(f"{t}.{c}" for t, cs in WRITABLE.items() for c in sorted(cs))
            item.message = f"변경 불가 항목: {cr.table}.{cr.column}은(는) 변경 허용 대상이 아닙니다. 허용 항목: {allowed}"
            return False
        desc = json.loads(tool_output_text(await ro["describe_table"].ainvoke({"table": cr.table})))
        allowed_vals = desc["allowed_values"].get(cr.column, [])
        if cr.new_value not in allowed_vals:
            item.message = (f"허용되지 않은 값: {cr.table}.{cr.column}에 '{cr.new_value}'은(는) 허용 코드값이 아닙니다. "
                            f"허용값: {allowed_vals}. 올바른 값으로 다시 요청해 주세요.")
            return False
        return True

    async def process_one(item: ChangeItem, request: str, config) -> ChangeItem:
        cr = item.request
        if not await precheck(item):
            item.status = "blocked"
            return item

        ws: dict = {}
        sup = build_change_supervisor(cr, ws, ro)
        await sup.ainvoke({"messages": [("user", supervisor_input(cr, request))]},
                          config={"callbacks": config.get("callbacks"), "recursion_limit": 40})
        a = ws.get("analysis", {})
        log("supervisor_result", index=item.index, analysis_status=a.get("status"), sql=ws.get("sql"),
            review=ws.get("review"), sql_attempts=ws.get("sql_attempts"))

        status = a.get("status")
        if status in ("not_found", "ambiguous"):
            item.status, item.candidates = status, a.get("candidates", [])
            return item
        if status == "over_limit":
            item.status = "over_limit"
            item.message = (f"영향 건수 한도 초과: 대상 {a['count']}건이 1회 변경 한도 {MAX_AFFECTED}건을 넘습니다. "
                            f"{MAX_AFFECTED}건 이하로 분할해(예: 주문번호 범위·일자 구간별) 각각 요청·승인받아 주세요.")
            return item
        if status != "found":
            item.status, item.message = "failed", "대상 분석 결과를 확정하지 못했습니다. 요청을 구체화해 다시 요청해 주세요."
            return item
        item.where_clause, item.snapshot = a["where"], a["to_change"]
        if not item.snapshot:
            item.status = "no_change"
            return item

        # ---- 코드 게이트: Reviewer 결과와 무관하게 다시 검증 ----
        sql = ws.get("sql") or ""
        review = ws.get("review") or {}
        item.sql, item.review = sql, review.get("comment", "")
        violations = check_sql(sql, cr.table, {cr.column}) if sql else ["SQL 미제출"]
        if violations or ws.get("sql_violations"):
            item.status, item.message = "failed", f"SQL 정적검사 불합격: {violations or ws.get('sql_violations')}"
            return item
        if not review.get("approved") or review.get("sql") != sql:
            item.status, item.message = "failed", f"Reviewer 반려: {review.get('comment', '검토 결과 없음')}"
            return item

        item.rollback_statements = rollback_statements(cr, item.snapshot)
        dr = json.loads(tool_output_text(await ex["dry_run"].ainvoke(
            {"sql": sql, "rollback_statements": item.rollback_statements, "table": cr.table}, config=config)))
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
        if problems:
            item.status, item.message = "failed", "dry-run 검증 실패: " + "; ".join(problems)
            return item

        item.warnings = history_warnings(store, cr, expected)
        item.status = "ready"
        return item

    async def process(state: SRState, config):
        items = []
        for raw in state["items"]:            # Plan-Execute: 계획된 하위 변경을 순서대로 실행
            item = await process_one(ChangeItem(**raw), state["request"], config)
            log("change_item", index=item.index, status=item.status, message=mask_pii(item.message))
            items.append(item.model_dump())
            if item.status in BLOCKING:       # all-or-nothing: 하나라도 막히면 나머지는 처리하지 않음
                break
        done = {i["index"] for i in items}
        for raw in state["items"]:
            if raw["index"] not in done:
                items.append({**raw, "status": "blocked", "message": "선행 하위 변경이 차단되어 처리하지 않았습니다."})
        return {"items": items}

    def route_after_process(state: SRState):
        items = [ChangeItem(**r) for r in state["items"]]
        if not items or any(i.status in BLOCKING for i in items):
            return "report"
        if any(i.status == "ready" for i in items) and all(i.status in ("ready", "no_change") for i in items):
            return "approval"
        return "report"

    async def approval(state: SRState):
        items = [ChangeItem(**r) for r in state["items"]]
        card = guard_output(render_card(items))
        decision = interrupt({"card": card})            # HITL: 사람이 승인/거절할 때까지 정지
        decision = decision if decision in ("approve", "reject") else "reject"
        log("approval", decision=decision)
        return {"card": card, "decision": decision}

    async def execute(state: SRState, config):
        items = [ChangeItem(**r) for r in state["items"]]
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

        if not ok:                                     # 부분 실행분 원복 (SR 단위 all-or-nothing)
            for it in done:
                for stmt in it.rollback_statements:
                    await ex["execute_change"].ainvoke({"sql": stmt.rstrip(";"), "expected_rowcount": 1}, config=config)
                it.status, it.message = "failed", "SR 내 다른 변경 실패로 롤백됨"
        else:                                          # 변경 이력 Store 저장 (장기 메모리)
            at = time.strftime("%Y-%m-%dT%H:%M:%S")
            for it in done:
                for r in it.snapshot:
                    store.put((HISTORY_NS, it.request.table, r["pk"]), f"{it.request.column}@{at}",
                              {"column": it.request.column, "old": r["old"], "new": it.request.new_value,
                               "reason": it.request.reason or "미기재", "at": at,
                               "request": mask_pii(state["request"])})
        log("execute", ok=ok, executed=[i.index for i in done])
        return {"items": [i.model_dump() for i in items], "executed": ok}

    async def report(state: SRState):
        items = [ChangeItem(**r) for r in state.get("items", [])]
        text = guard_output(render_report(state, items))
        log("report", text=text[:500])
        return {"report": text}

    g = StateGraph(SRState)
    for name, fn in [("input_guard", input_guard), ("plan", plan), ("process", process),
                     ("approval", approval), ("execute", execute), ("report", report)]:
        g.add_node(name, fn)
    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", lambda s: "report" if s.get("blocked_message") else "plan")
    g.add_edge("plan", "process")
    g.add_conditional_edges("process", route_after_process)
    g.add_conditional_edges("approval", lambda s: "execute" if s["decision"] == "approve" else "report")
    g.add_edge("execute", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=InMemorySaver())


# ---------------- 공통 인터페이스 ----------------
def make_tracer(path) -> FileTracer:
    return FileTracer(path, mask=True)


async def run_request(request: str, *, db_path, history_path, approval_fn, callbacks) -> dict:
    tracer = next((c for c in callbacks if isinstance(c, FileTracer)), None)
    with SqliteStore.from_conn_string(str(history_path)) as store:
        store.setup()
        async with mcp_tools(db_path) as servers:
            graph = build_graph(servers, store, tracer)
            cfg = {"configurable": {"thread_id": str(uuid.uuid4())}, "callbacks": callbacks, "recursion_limit": 50}
            state = await graph.ainvoke({"request": request}, cfg)
            card = ""
            if state.get("__interrupt__"):
                card = state["__interrupt__"][0].value["card"]
                decision = approval_fn(card) or "reject"
                state = await graph.ainvoke(Command(resume=decision), cfg)

    items = [ChangeItem(**r) for r in state.get("items", [])]
    executed = [i for i in items if i.status == "executed"]
    plan = ChangePlan(request=mask_pii(request), items=items, report=state.get("report", ""),
                      status=("executed" if executed else "rejected" if state.get("decision") == "reject"
                              else "no_change" if items and all(i.status == "no_change" for i in items) else "blocked"))
    rollback = "\n".join(s for i in executed for s in i.rollback_statements) or None
    output = "\n\n".join(t for t in (card, plan.report) if t)
    return {"output": output, "rollback_sql": rollback, "plan": plan.model_dump()}
