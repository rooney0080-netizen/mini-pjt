# SR-Fixer

> 운영 데이터 수동 변경(SR) 처리 에이전트

현업의 데이터 보정 요청("주문 ORD-20231 결제상태를 결제완료로 바꿔주세요")을 받아
**대상 조회 → UPDATE·롤백 SQL 생성 → 영향 건수 검증 → 사람 승인 → 실행 → 이력 저장**까지 안전하게 처리합니다.

자세한 서비스 정의·가드레일·평가 결과는 [SERVICE.md](SERVICE.md)를 참고하세요.

## 과제 개요

- Day1~7에서 배운 에이전트 패턴을 **6개 이상** 적용 (본 프로젝트는 11개 적용)
- 제출물: `SERVICE.md` + 에이전트 코드 + 인-아웃 세트 평가 결과(1차 → 2차)
- 도메인: 쇼핑몰 주문/결제/회원 운영 DB, 변경 유형은 **UPDATE만** 지원

## 핵심 아이디어

**LLM은 해석·생성·설명만, 안전장치는 코드가 강제한다.**

- 실행 도구는 어떤 LLM에도 주지 않고, 승인(HITL) 이후 그래프 노드 코드만 호출
- MCP 서버를 `ops-readonly`(조회) / `ops-exec`(dry-run·실행)로 권한 분리
- 롤백 SQL은 변경 전 스냅샷에서 코드가 생성하고, dry-run으로 복원까지 검증
- 화이트리스트 컬럼·허용 코드값·영향 건수 한도(10건)·SQL 정적검사(sqlglot)로 차단

## 아키텍처

```
요청 → 입력 가드 → Planner(복합 SR 분해)
     → Supervisor [analyst · sql · reviewer Agent] → SQL 정적검사
     → dry-run(건수·롤백 검증) → 승인(interrupt) → 실행 → 사후 검증
     → 이력 Store 저장 → 출력 가드(PII 마스킹) → 리포트
```

## 적용 패턴

| 패턴 | 적용 내용 |
|---|---|
| LCEL + Pydantic | 요청 → `ChangeRequest` 리스트, 결과 `ChangePlan` |
| ReAct | analyst_agent가 조회 도구를 자율 선택 |
| 도구 다중 / MCP | `describe_table`, `query_readonly`, `count_affected`, `dry_run` / 서버 2개 분리 |
| 가드레일 | 입력(인젝션) · SQL 정적검사 · 출력(PII 마스킹) |
| HITL | 실행 직전 `interrupt()` 승인/거절 |
| 미들웨어 | 재시도, 도구 결과 PII 마스킹 |
| Supervisor | analyst / sql / reviewer |
| Plan-Execute + Store | 복합 SR 분해 후 순차 처리, 변경 이력 `SqliteStore` |
| Trace / 평가 | `FileTracer` JSONL, DB diff 채점 + LLM-Judge |

## 평가 결과 (v1 → v2)

- **v1**: 프롬프트 규칙만 있는 단일 ReAct 에이전트 (실행 도구를 LLM이 직접 보유)
- **v2**: 위 아키텍처 전체

| 지표 | v1 | v2 |
|---|---|---|
| 케이스 통과율 | 75% (15/20) | **100% (20/20)** |
| 가드레일 차단율 | 66.7% (6/9) | **100% (9/9)** |
| 무승인 실행 건수 | 0 | 0 |
| 설명 품질 (LLM-Judge) | 4.53 | **5.00** |

## 실행 방법

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
# .env 에 AWS Bedrock 자격증명 설정 (us-east-1)

.venv/Scripts/python data/make_db.py                 # 샘플 운영 DB 생성
.venv/Scripts/python main.py --reset "ORD-20231 결제상태를 결제완료로 변경해주세요"   # CLI 데모 (y/n 승인)
.venv/Scripts/python eval/run_eval.py --version v2   # 평가 → eval/results_v2.json
```

## 폴더 구조

```
mini-prj/
├─ main.py            # CLI 진입점
├─ SERVICE.md         # 서비스 정의·가드레일·평가 결과
├─ data/              # 샘플 운영 DB 생성
├─ eval/              # 인-아웃 세트, 평가 러너, LLM-Judge, 결과
└─ src/
   ├─ workflows/      # v2 SR 처리 그래프
   ├─ intake/         # 입력 가드, Planner
   ├─ policy/         # 화이트리스트, SQL 정적검사
   ├─ drafting/       # Supervisor + 3 Agent
   ├─ execution/      # dry-run, 실행
   ├─ history/        # 변경 이력 Store
   ├─ reporting/      # 승인 카드·리포트, 출력 가드
   ├─ tools/          # MCP 서버·클라이언트
   ├─ baseline/       # v1 기준선
   └─ shared/         # 설정, 미들웨어, tracer, 유틸
```

## 기술 스택

Python 3.14 · LangChain / LangGraph · langgraph-supervisor · MCP (fastmcp) · Amazon Bedrock (Claude Sonnet 4.5) · SQLite · sqlglot
