"""baseline/agent.py - 기준선: 프롬프트 규칙만 있는 단일 ReAct 에이전트

- 가드레일은 시스템 프롬프트에만 존재하고 코드 강제는 없다.
- LLM이 조회 도구와 실행 도구(execute_sql)를 모두 직접 가진다.
- 승인은 대화로 받는다: 1턴에서 에이전트가 승인을 요청하면, 사용자가 다음 메시지로 승인/거절한다.
"""
import re

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage

from src.baseline.prompts import SYSTEM_PROMPT
from src.shared.config import make_llm
from src.shared.messages import get_text
from src.shared.tracer import FileTracer
from src.tools.client import mcp_tools

ROLLBACK_RE = re.compile(r"```rollback\s*(.*?)```", re.S)
PROPOSAL_RE = re.compile(r"```(sql|rollback)", re.I)   # 응답에 SQL 블록이 있으면 '변경 제안'으로 본다
MAX_APPROVAL_ROUNDS = 3


def _executed(messages) -> bool:
    return any(c["name"] == "execute_sql" for m in messages if isinstance(m, AIMessage)
               for c in (m.tool_calls or []))


def make_tracer(path) -> FileTracer:
    return FileTracer(path, mask=False)


def _ai_texts(messages) -> list[str]:
    return [t for m in messages if isinstance(m, AIMessage) and (t := get_text(m).strip())]


async def run_request(request: str, *, db_path, history_path, approval_fn, callbacks) -> dict:
    """v1/v2 공통 인터페이스.

    approval_fn(card_text) -> "approve" | "reject" | None  (평가 러너가 케이스 정의대로 응답)
    반환: {"output": 사용자에게 보인 전체 텍스트, "rollback_sql": 추출한 롤백 SQL 또는 None}
    """
    config = {"callbacks": callbacks, "recursion_limit": 40}
    async with mcp_tools(db_path) as servers:
        tools = list(servers["ops-readonly"].values()) + [servers["ops-exec"]["execute_sql"]]
        agent = create_agent(model=make_llm(), tools=tools, system_prompt=SYSTEM_PROMPT)

        result = await agent.ainvoke({"messages": [HumanMessage(request)]}, config=config)
        messages = result["messages"]
        turn = messages

        # 대화형 승인: 1턴 응답 후 운영자가 응답하고, 이후에도 에이전트가 (실행 없이) 새 변경 제안을
        # 내놓으면 운영자가 다시 응답한다 (최대 MAX_APPROVAL_ROUNDS회)
        for round_no in range(MAX_APPROVAL_ROUNDS):
            turn_text = "\n\n".join(_ai_texts(turn))
            if round_no > 0 and (_executed(turn) or not PROPOSAL_RE.search(turn_text)):
                break
            decision = approval_fn(turn_text)
            if not decision:
                break
            reply = "승인합니다. 실행해 주세요." if decision == "approve" else "거절합니다. 실행하지 마세요."
            before = len(messages)
            result = await agent.ainvoke({"messages": messages + [HumanMessage(reply)]}, config=config)
            messages = result["messages"]
            turn = messages[before + 1:]

    texts = _ai_texts(messages)
    output = "\n\n".join(texts)
    blocks = ROLLBACK_RE.findall(output)
    return {"output": output, "rollback_sql": blocks[-1].strip() if blocks else None}
