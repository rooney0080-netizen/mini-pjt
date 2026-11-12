"""drafting/supervisor.py - 하위 변경 1건을 처리하는 Supervisor + 전문 Agent 3종

  analyst_agent  : read-only MCP 도구로 대상 레코드를 찾고 submit_targets로 제출 (ReAct)
  sql_agent      : 검증된 대상에 대한 UPDATE 1문장을 submit_sql로 제출
  reviewer_agent : 요청·대상·SQL·정적검사 결과를 보고 submit_review로 승인/반려

결과는 제출 도구(submit_tools.py)를 통해 workspace(dict)에 기록된다.
어떤 Agent도 실행(ops-exec) 도구를 갖지 않는다.
"""
from langchain.agents import create_agent
from langgraph_supervisor import create_supervisor

from src.drafting.prompts import ANALYST_PROMPT, REVIEWER_PROMPT, SQL_PROMPT, SUPERVISOR_PROMPT
from src.drafting.submit_tools import make_submit_tools
from src.intake.schemas import ChangeRequest
from src.policy.rules import PK
from src.shared.config import make_llm
from src.shared.middleware import default_middleware


def build_change_supervisor(cr: ChangeRequest, ws: dict, ro: dict):
    """하위 변경 1건 전용 Supervisor를 만든다. ws(workspace)에 analysis / sql / review 결과가 쌓인다."""
    st = make_submit_tools(cr, ws, ro)
    llm = make_llm()
    analyst = create_agent(model=llm, name="analyst_agent", system_prompt=ANALYST_PROMPT,
                           tools=[ro["describe_table"], ro["query_readonly"], ro["count_affected"], st["submit_targets"]],
                           middleware=default_middleware())
    sql_agent = create_agent(model=llm, name="sql_agent", system_prompt=SQL_PROMPT,
                             tools=[st["submit_sql"]], middleware=default_middleware())
    reviewer = create_agent(model=llm, name="reviewer_agent", system_prompt=REVIEWER_PROMPT,
                            tools=[st["get_review_packet"], st["submit_review"]], middleware=default_middleware())
    # checkpointer=False: 바깥 그래프(HITL용 checkpointer)를 상속하지 않는 일회성 서브그래프
    return create_supervisor([analyst, sql_agent, reviewer], model=llm,
                             prompt=SUPERVISOR_PROMPT).compile(checkpointer=False, name="change_supervisor")


def supervisor_input(cr: ChangeRequest, request_text: str) -> str:
    return (f"[SR 원문]\n{request_text}\n\n[처리할 하위 변경]\n{cr.model_dump_json(indent=1)}\n\n"
            f"[참고] {cr.table}의 PK 컬럼은 {PK[cr.table]}, 변경 컬럼은 {cr.column}, 목표 값은 '{cr.new_value}'.")
