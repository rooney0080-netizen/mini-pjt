"""policy/rules.py - 변경 정책 상수 (v2는 코드로 강제, v1 기준선은 프롬프트로만 전달)"""

PK = {"orders": "order_id", "members": "member_id", "payments": "payment_id"}

WRITABLE = {                                # 변경 가능한 테이블·컬럼 화이트리스트 (PK·금액 컬럼 제외)
    "orders": {"order_status", "payment_status", "settle_status"},
    "members": {"grade", "status"},
}
WRITABLE_DESC = ", ".join(f"{t}.{c}" for t, cols in WRITABLE.items() for c in sorted(cols))

MAX_AFFECTED = 10                           # 한 번에 변경 가능한 최대 행 수
