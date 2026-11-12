"""main.py - SR-Fixer CLI 데모 (v2)

사용:
  python main.py "ORD-20231 결제상태가 결제실패인데 PG 승인 확인됨. 결제완료로 변경 부탁드립니다."
  python main.py --reset "..."      # 작업 DB(data/ops.db)를 초기 상태로 되돌린 뒤 실행
  python main.py                    # 요청을 대화형으로 입력

작업 DB: data/ops.db (없으면 data/ops_init.db 복사), 이력 Store: data/history.sqlite,
trace: logs/trace_<시각>.jsonl (PII 마스킹 후 기록)
"""
import argparse
import asyncio
import shutil
import sys
import time

from src.shared.config import DEFAULT_DB, DEFAULT_HISTORY, INIT_DB, LOG_DIR
from src.workflows.sr_graph import make_tracer, run_request


def ask_approval(card: str) -> str:
    print("\n" + card + "\n")
    while True:
        ans = input("승인하시겠습니까? [y=승인 / n=거절] > ").strip().lower()
        if ans in ("y", "yes", "승인"):
            return "approve"
        if ans in ("n", "no", "거절"):
            return "reject"


async def main():
    ap = argparse.ArgumentParser(description="운영 데이터 변경(SR) 처리 에이전트")
    ap.add_argument("request", nargs="?", help="SR 요청 문장")
    ap.add_argument("--reset", action="store_true", help="작업 DB와 이력을 초기 상태로 되돌림")
    args = ap.parse_args()

    if not INIT_DB.exists():
        sys.exit("data/ops_init.db 가 없습니다. 먼저 python data/make_db.py 를 실행하세요.")
    if args.reset or not DEFAULT_DB.exists():
        shutil.copy(INIT_DB, DEFAULT_DB)
        DEFAULT_HISTORY.unlink(missing_ok=True)
        print("작업 DB를 초기 상태로 준비했습니다.")
    request = args.request or input("SR 요청 > ").strip()

    tracer = make_tracer(LOG_DIR / f"trace_{time.strftime('%Y%m%d_%H%M%S')}.jsonl")
    print("처리 중... (대상 분석 -> SQL 작성 -> 검토 -> dry-run)")
    result = await run_request(request, db_path=str(DEFAULT_DB), history_path=str(DEFAULT_HISTORY),
                               approval_fn=ask_approval, callbacks=[tracer])
    print("\n" + result["plan"]["report"])
    print(f"\n(trace: {tracer.path})")


if __name__ == "__main__":
    asyncio.run(main())
