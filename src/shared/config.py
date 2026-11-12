"""shared/config.py - 경로, 모델, 공용 LLM 클라이언트 (모든 도메인 공용)

변경 정책(화이트리스트·한도·PK)은 src/policy/rules.py, 이력 Store 네임스페이스는 src/history/store.py에 있다.
"""
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
INIT_DB = DATA_DIR / "ops_init.db"          # make_db.py가 만드는 초기 상태 (평가 기준점)
DEFAULT_DB = DATA_DIR / "ops.db"            # 데모(main.py)용 작업 DB
DEFAULT_HISTORY = DATA_DIR / "history.sqlite"
LOG_DIR = ROOT / "logs"

MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
REGION = "us-east-1"


def make_llm():
    """모든 LLM 호출(Planner, Supervisor, 하위 Agent, Judge) 공용.
    botocore 적응형 재시도로 Bedrock ThrottlingException을 클라이언트 단에서 흡수한다
    (ModelRetryMiddleware는 create_agent 기반 하위 Agent에만 붙기 때문에 Planner·Supervisor는 이것으로 보호)."""
    from botocore.config import Config
    from langchain_aws import ChatBedrockConverse
    return ChatBedrockConverse(model=MODEL_ID, region_name=REGION, temperature=0,
                               config=Config(retries={"max_attempts": 10, "mode": "adaptive"}))
