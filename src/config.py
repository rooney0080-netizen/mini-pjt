"""config.py - 경로, 모델, 변경 정책(화이트리스트·한도) 상수"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
INIT_DB = DATA_DIR / "ops_init.db"          # make_db.py가 만드는 초기 상태 (평가 기준점)
DEFAULT_DB = DATA_DIR / "ops.db"            # 데모(main.py)용 작업 DB
DEFAULT_HISTORY = DATA_DIR / "history.sqlite"
LOG_DIR = ROOT / "logs"

MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
REGION = "us-east-1"

# ---- 변경 정책 (v2에서 코드로 강제, v1은 프롬프트로만 전달) ----
PK = {"orders": "order_id", "members": "member_id", "payments": "payment_id"}
WRITABLE = {                                # 변경 가능한 테이블·컬럼 화이트리스트
    "orders": {"order_status", "payment_status", "settle_status"},
    "members": {"grade", "status"},
}
MAX_AFFECTED = 10                           # 한 번에 변경 가능한 최대 행 수

# 이력 Store 네임스페이스: ("sr_history", table, pk) / key: f"{column}@{iso시각}"
HISTORY_NS = "sr_history"


def make_llm():
    from langchain_aws import ChatBedrockConverse
    return ChatBedrockConverse(model=MODEL_ID, region_name=REGION, temperature=0)
