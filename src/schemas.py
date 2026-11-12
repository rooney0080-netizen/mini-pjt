"""schemas.py - 요청 파싱(ChangeRequest)과 처리 결과(ChangePlan) Pydantic 모델"""
from typing import Literal

from pydantic import BaseModel, Field


class ChangeRequest(BaseModel):
    """SR 안의 하위 변경 1건"""
    operation: Literal["update", "delete", "insert", "other"] = Field(
        description="요청된 작업 종류. 값 변경=update, 삭제=delete, 추가=insert, 그 외=other")
    table: str = Field(description="대상 테이블 (members, orders, payments 중 하나. 모르면 unknown)")
    column: str = Field(description="변경할 DB 컬럼명 (예: payment_status). 매핑 표에 없으면 요청 표현 그대로")
    new_value: str = Field(description="변경 후 값. 요청 원문 표현 그대로 (교정·추측 금지)")
    target_desc: str = Field(description="대상 설명 원문 (예: 'ORD-20231', '3월 미정산 주문 전부', '김민수 회원')")
    key_values: list[str] = Field(default_factory=list,
                                  description="요청에 명시된 PK 값 목록 (예: ['ORD-20231']). 명시되지 않았으면 빈 리스트")
    reason: str | None = Field(default=None, description="요청에 적힌 변경 사유. 없으면 null")


class ParsedSR(BaseModel):
    """SR 1건을 하위 변경 목록으로 분해한 계획 (Plan-Execute의 Plan)"""
    changes: list[ChangeRequest] = Field(description="요청에 들어 있는 변경 작업 목록 (요청 순서대로)")


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
