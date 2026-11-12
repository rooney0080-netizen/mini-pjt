"""workflows/sr_graph.py - v2: 고정 순서 StateGraph (Plan-Execute + Supervisor + HITL + Store)

  input_guard -> plan -> process(하위 변경마다: precheck -> Supervisor -> 코드 검증 -> dry-run)
              -> approval(interrupt) -> execute(코드) -> report
  차단·대상 없음·모호·한도 초과 등이 하나라도 있으면 SR 전체를 실행하지 않고 report로 간다 (all-or-nothing).

각 단계의 로직은 도메인 폴더에 있고, 이 파일은 노드 연결과 상태 전이만 담당한다.
실행 도구(ops-exec)는 어떤 LLM에도 바인딩되지 않으며 execute 노드만 호출한다.
"""
import os
import uuid

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.store.sqlite import SqliteStore
from langgraph.types import Command, interrupt

from src.drafting.supervisor import build_change_supervisor, supervisor_input
from src.execution.dry_run_gate import verify_with_dry_run
from src.execution.executor import execute_items
from src.history.store import history_warnings, record_changes
from src.intake.input_guard import check_input
from src.intake.planner import make_planner
from src.intake.schemas import ParsedSR
from src.policy.precheck import precheck
from src.policy.rules import MAX_AFFECTED
from src.policy.sql_guard import check_sql
from src.reporting.templates import guard_output, render_card, render_report
from src.shared.pii import mask_pii
from src.shared.tracer import FileTracer
from src.tools.client import mcp_tools
from src.workflows.schemas import BLOCKING, ChangeItem, ChangePlan, SRState


def build_graph(servers: dict, store, tracer: FileTracer | None):
    ro, ex = servers["ops-readonly"], servers["ops-exec"]
    planner = make_planner()

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

    async def process_one(item: ChangeItem, request: str, config) -> ChangeItem:
        cr = item.request
        blocked = await precheck(cr, ro)
        if blocked:
            item.status, item.message = "blocked", blocked
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

        problems = await verify_with_dry_run(item, ex, config)
        if problems:
            item.status, item.message = "failed", "dry-run 검증 실패: " + "; ".join(problems)
            return item

        item.warnings = history_warnings(store, cr, sorted(r["pk"] for r in item.snapshot))
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
        ok, done = await execute_items(items, ex, ro, config)
        if ok:                                          # 변경 이력 Store 저장 (장기 메모리)
            record_changes(store, done, state["request"])
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


# ---------------- 공통 인터페이스 (eval/run_eval.py, main.py) ----------------
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
