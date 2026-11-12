"""workflows/schemas.py - SR 처리 그래프의 상태 타입 (하위 변경 처리 상태 ChangeItem, 최종 결과 ChangePlan)"""
from typing import Literal, TypedDict

from pydantic import BaseModel, Field

from src.intake.schemas import ChangeRequest

ItemStatus = Literal["ready", "blocked", "not_found", "ambiguous", "over_limit", "no_change", "executed", "failed"]


class ChangeItem(BaseModel):
    """하위 변경 1건의 처리 상태"""
    index: int
    request: ChangeRequest
    status: ItemStatus = "blocked"
    message: str = ""
    where_clause: str = ""
    snapshot: list[dict] = Field(default_factory=list, description="변경 대상 행의 변경 전 값 [{pk, old}]")
    candidates: list[str] = Field(default_factory=list)
    sql: str = ""
    rollback_statements: list[str] = Field(default_factory=list)
    dry_run: dict = Field(default_factory=dict)
    review: str = ""
    warnings: list[str] = Field(default_factory=list)
    exec_result: dict = Field(default_factory=dict)


class ChangePlan(BaseModel):
    """SR 처리 최종 결과 (리포트의 원천 데이터)"""
    request: str
    status: Literal["executed", "rejected", "blocked", "no_change", "failed"]
    items: list[ChangeItem] = Field(default_factory=list)
    report: str = ""


# 하나라도 이 상태면 SR 전체를 실행하지 않는다 (all-or-nothing)
BLOCKING = {"blocked", "not_found", "ambiguous", "over_limit", "failed"}


class SRState(TypedDict, total=False):
    request: str
    blocked_message: str
    items: list[dict]
    card: str
    decision: str
    executed: bool
    report: str
