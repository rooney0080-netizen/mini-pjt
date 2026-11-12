"""intake/planner.py - Planner: SR 원문을 하위 변경(ChangeRequest) 목록으로 분해 (LCEL + Pydantic 구조화 출력)"""
from src.intake.prompts import PLANNER_PROMPT
from src.intake.schemas import ParsedSR
from src.shared.config import make_llm


def make_planner():
    """{"request": str} -> ParsedSR"""
    return PLANNER_PROMPT | make_llm().with_structured_output(ParsedSR)
