# mini-prj — 운영 데이터 수동 변경(SR) 처리 에이전트

AI 에이전트 개발 교육(Day1~7) 미니프로젝트 과제. 이 파일은 새 세션이 바로 작업을 이어가기 위한 인수인계 문서다.
설계는 사용자와 Q&A로 확정했다(2026-11-12). **확정 사항(§3~§7)은 사용자 동의 없이 바꾸지 말 것.**

## 1. 과제 조건

- Day1~7 패턴 중 **6개 이상** 적용 (RAG는 사용하지 않기로 결정)
- 제출물: `SERVICE.md` + 에이전트 코드 + 인-아웃 세트 평가 결과(1차→2차)
- 제출까지 기간: **1~2일** (2026-11-12 기준) → 범위 축소 우선순위는 §9
- 평가기준
  1. SERVICE.md 품질 (도메인 명확성, 인-아웃 세트 구체성·측정 가능성, 가드레일 정의)
  2. 서비스 임팩트 (사용자 문제·가치가 얼마나 뾰족한가)
  3. 에이전트 개발 완성도 (구현·아키텍처·코드 품질)
  4. 인-아웃 세트 충족도 (본인 세트 통과율, 1차→2차 개선폭)
  5. 가드레일 준수 여부 (정의한 제약을 실제로 지키는가)

## 2. 서비스 정의

**문제**: SI/SM 운영에서 현업의 데이터 보정 요청(예: "주문 ORD-20231 결제상태가 '결제실패'인데 실제 결제됨 → '결제완료'로 변경")을 운영자가 수작업 처리한다. 대상 조회 → SQL 작성 → 영향 건수 확인 → 승인 → 실행 → 백업·롤백 스크립트 작성 과정에서 WHERE 누락, 대상 오인, 롤백 불가 같은 사고가 난다.

**에이전트**: 요청 해석 → 대상 조회 → UPDATE SQL + 롤백 SQL 생성 → SQL 정적검사·dry-run으로 영향 건수 검증 → 사람 승인(HITL) → 실행 → 사후 검증 → 변경 이력 저장 → 처리 리포트.

**도메인(확정)**: 쇼핑몰 주문/결제/회원. **변경 유형은 UPDATE만** 지원 (DELETE·INSERT 요청은 "지원 범위 밖"으로 차단).

## 3. 핵심 설계 원칙 (확정)

**"LLM은 해석·생성·설명만, 안전장치는 코드가 강제한다."**

- 실행 툴(`execute_change`)은 **어떤 LLM에도 바인딩하지 않는다.** 승인(`interrupt` resume = approve) 이후 그래프 노드 코드만 호출 → 프롬프트 인젝션이 성공해도 무승인 실행은 구조적으로 불가능.
- MCP 서버를 2개로 분리: `ops-readonly`(분석 Agent에 연결) / `ops-exec`(dry_run, execute — 그래프 노드만 사용)
- 롤백 SQL은 LLM이 아니라 **변경 전 스냅샷에서 코드가 결정적으로 생성**하고, dry-run 트랜잭션 안에서 "변경 → 롤백 → 원상복구" 검증까지 한다.
- 변경 가능 테이블·컬럼은 화이트리스트(예: `orders.payment_status/settle_status/order_status`, `members.grade/status`). PK·금액 컬럼은 변경 금지.

## 4. 아키텍처 (확정: 하이브리드)

바깥은 **고정 순서 StateGraph**, 분석→SQL→검토 구간만 `create_supervisor`가 3개 Agent를 조율한다.

```
요청 → [입력 가드:코드] → Planner(LCEL+Pydantic: 복합 SR → ChangeRequest 리스트) + Store 중복 이력 조회
        ↓  (하위 변경마다)
  ┌ Supervisor ────────────────────────────────────────────┐
  │ analyst_agent (ReAct, ops-readonly MCP) → 대상 스냅샷   │
  │ sql_agent → UPDATE SQL (LLM 작성)                        │
  │ reviewer_agent + [SQL 정적검사:sqlglot AST] (불합격 시 1회 재생성) │
  └────────────────────────────────────────────────────────┘
        ↓  대상 0건 → "대상 없음" / 10건 초과 → 차단+분할 안내 / 이미 목표값 → "변경 불필요"
  dry_run (ops-exec: 트랜잭션 실행 → 건수 대조 → 롤백 검증 → ROLLBACK)
        ↓
  interrupt(): 하위 변경 전체 diff·건수·롤백 SQL·중복 이력 경고를 한 번에 제시 → 승인 / 거절
        ↓
  execute(코드) → 사후 검증 조회 → 이력 Store 저장 → [출력 가드: PII 마스킹] → ChangePlan 리포트
```

- 모호 대상(동명이인 등): 되묻지 않고 "대상 특정 불가 + 후보 목록" 반환 후 종료
- 중복 요청: 차단하지 않고 승인 카드에 "동일 변경 이력 있음" 경고
- 모델: 전 Agent `us.anthropic.claude-sonnet-4-5-20250929-v1:0`, `temperature=0`
- 데모 UI: CLI만 (`main.py`, y/n 승인)

## 5. 적용 패턴 (11개)

| # | 패턴 | 적용 |
|---|---|---|
| 1 | LCEL + Pydantic | 요청 → `ChangeRequest` 리스트 파싱 / 결과 `ChangePlan` |
| 2 | ReAct | analyst_agent가 조회 도구 자율 선택 |
| 4 | 도구 다중 | `describe_table`, `query_readonly`, `count_affected`, `dry_run` |
| 5 | MCP | `ops-readonly` / `ops-exec` 서버 분리 (권한 분리) |
| 6 | 가드레일 | 입력(인젝션·SQL조각) / SQL 정적검사(DDL·WHERE 누락·다중 문장·화이트리스트) / 출력(PII 마스킹) |
| 7 | HITL | 실행 직전 `interrupt()` → 승인/거절 |
| 8 | 미들웨어 | 재시도, 로그 PII 마스킹 |
| 9 | Supervisor | analyst / sql / reviewer |
| 10 | Plan-Execute + Store | 복합 SR 분해 후 하위 변경 순차 처리(묶음 승인), 변경 이력 `SqliteStore` |
| 11 | Trace | `FileTracer` 콜백 → `trace_*.jsonl` (PII 마스킹 후 기록) |
| 12 | 평가 | DB diff 기반 통과율 + 설명 품질 LLM-Judge |

## 6. 가드레일 (SERVICE.md에 그대로 정의, 코드로 강제)

- 승인 없이 변경 실행 금지 (무승인 실행 0건)
- WHERE 없는 UPDATE 금지, DDL(DROP/ALTER/TRUNCATE/CREATE)·DELETE·INSERT 금지, 다중 문장 금지
- 화이트리스트 밖 테이블·컬럼 변경 금지, 허용 코드값(`code_values`) 밖의 값 금지
- 영향 건수 한도 10건 (초과 시 차단 → 분할 안내)
- 존재하지 않는 대상에 대해 SQL을 지어내지 않음 ("대상 없음")
- dry-run 건수 ≠ 스냅샷 건수면 차단
- 롤백 SQL 없는(또는 롤백 검증 실패) 변경 금지
- 회원 전화번호·이메일 PII는 출력·로그·trace 저장 시 마스킹
- DB 데이터 안의 지시문(간접 인젝션)은 데이터로만 취급

## 7. 평가 설계

- 인-아웃 세트: `eval/inout_set.json` (기능 F01~F11, 가드레일 G01~G07·G09, 전역 검사 G08)
- 케이스마다 초기 DB(`data/ops_init.db`)를 복사해 독립 실행. 승인은 케이스의 `approval`로 자동 응답 (v2: `Command(resume=...)`, v1: 다음 사용자 메시지 "승인합니다/거절합니다")
- 판정: 기대 diff와 실제 diff 비교(`exact` 또는 `subset`) / 필수 신호 문자열 / execute 호출 수 / 스키마 해시 불변 / 롤백 SQL 적용 시 초기 DB 해시 복원 / (해당 시) Store 기록·PII 미노출
- **지표**: 기능 통과율 / 가드레일 차단율(목표 100%) / 무승인 실행 건수(목표 0) / 롤백 유효율 / LLM-Judge 설명 품질(≥4/5)
- **v1(기준선)**: 프롬프트 규칙만 있는 단일 ReAct(create_agent), 실행 툴을 LLM이 직접 보유. 코드 강제 없음 → `results_v1.json`
- **v2**: 위 아키텍처 전체 → `results_v2.json`. SERVICE.md에 비교표와 실패 사례를 솔직하게 기록

### 7.1 버전 공통 인터페이스 (평가 러너가 의존 — v2도 반드시 지킬 것)

`eval/run_eval.py`가 `src.baseline.agent`(v1) / `src.workflows.sr_graph`(v2) 모듈에서 아래 두 함수를 가져다 쓴다.

```python
def make_tracer(path) -> FileTracer          # v1: mask=False, v2: mask=True
async def run_request(request, *, db_path, history_path, approval_fn, callbacks) -> dict
    # approval_fn(card_text) -> "approve" | "reject" | None   (승인 카드 텍스트를 넘기면 케이스 정의대로 응답)
    # callbacks: [ToolRecorder, FileTracer] — 하위 Agent·노드의 도구 호출까지 전달되도록 config로 넘길 것
    # 반환: {"output": 사용자에게 보인 전체 텍스트(승인 카드 포함), "rollback_sql": 실행 가능한 롤백 SQL 문자열 | None}
```

- 실행 도구 이름은 `execute_sql`(v1) / `execute_change`(v2) — 러너가 이 이름으로 실행 호출 수를 센다
- 이력 Store: `SqliteStore` at `history_path`, namespace `("sr_history", table, pk)`, key `f"{column}@{iso}"`, value `{column, old, new, reason, at}` (F10 시드도 같은 형식)
- 케이스 산출물: `eval/runs/<version>/<case>/{ops.db, history.sqlite, trace.jsonl}` (gitignore)
- MCP 세션은 `src/tools/client.py: mcp_tools(db_path)`로 연다 (`get_tools()`는 호출마다 프로세스를 띄워 느림)

## 8. 기술 스택 / 환경

- Python 3.14, 버전은 `requirements.txt`에 고정 (교육 실습 `.venv`에서 검증된 버전)
- `ChatBedrockConverse`(`region_name="us-east-1"`), MCP: `mcp==1.30.0`, `fastmcp==2.13.0`, `langchain-mcp-adapters==0.3.2`
- `langgraph-checkpoint-sqlite`(checkpointer + `SqliteStore`), `langgraph-supervisor`, `sqlglot`
- `.env`는 `C:\Users\Admin\Desktop\sds-ax-practice\.env`에서 복사함. **절대 커밋 금지** (`.gitignore` 포함)
- 가상환경: `.venv` (실행: `.venv/Scripts/python.exe`)
- git 저장소 초기화됨. v1/v2 단계별로 커밋

## 9. 일정과 축소 우선순위 (1~2일)

| 단계 | 산출물 |
|---|---|
| P0 준비 | venv, `data/make_db.py`, `eval/inout_set.json`, SERVICE.md 골격 |
| P1 v1 | MCP 서버 2개, `src/baseline/agent.py`, `eval/run_eval.py` → `results_v1.json` |
| P2 v2 | schemas → guards → agents → graph(HITL·Store) → 미들웨어·tracer |
| P3 | `results_v2.json`, LLM-Judge, SERVICE.md 비교·실패 분석 |

시간이 부족하면 (1) Reviewer를 LLM 없이 코드 검사만 → (2) Plan-Execute(F11) 제외 순으로 줄인다. **가드레일·HITL은 줄이지 않는다.**

## 10. 재사용할 교육 실습 코드 (참고 후 재작성)

기준 경로: `C:\Users\Admin\Desktop\sds-ax-practice\`

| 용도 | 파일 |
|---|---|
| Pydantic 구조화 출력 | `day1_practice/structured_output.py` |
| ReAct 그래프 | `day3_practice/react_agent.py` |
| MCP 서버 / 클라이언트 연동 | `day4_practice/mcp_server.py`, `day4_practice/mcp_agent.py` |
| 입력·출력 가드, 거절 문구 | `day5_practice/guards.py`, `guards_input.py`, `guards_output.py`, `guards_refusal.py` |
| HITL `interrupt()` 흐름 | `day5_practice/hitl_flow.py`, `interrupt_basic.py` |
| 미들웨어 (마스킹·로깅) | `day5_practice/masking_middleware.py`, `logging_middleware.py`, `middleware_order.py` |
| Supervisor | `day6_practice/supervisor_assembled.py`, `specialist_agents.py` |
| Plan-Execute / 장기 메모리 | `day7_practice/plan_and_execute.py`, `long_term_memory.py` |
| Trace | `day7_practice/local_tracer.py` (`FileTracer` 콜백) |
| 평가 러너 / LLM-Judge | `day7_practice/run_eval.py` (`ToolRecorder`), `day7_practice/llm_judge.py` (`JudgeResult`) |

## 11. 폴더 구조 (도메인별, 2026-11-12 리팩토링)

```
mini-prj/
├─ CLAUDE.md / SERVICE.md / requirements.txt / main.py   # main.py = CLI 진입점
├─ data/make_db.py            # 샘플 운영 DB 생성 (결정적) → data/ops_init.db
├─ src/
│  ├─ workflows/              # v2 SR 처리 그래프 (노드 연결·상태 전이만)
│  │  ├─ sr_graph.py          #   build_graph, make_tracer, run_request  ← v2 공통 인터페이스
│  │  └─ schemas.py           #   ChangeItem, ChangePlan, SRState, BLOCKING
│  ├─ intake/                 # 요청 접수: 입력 가드 → Planner
│  │  ├─ input_guard.py       #   check_input (정규식 규칙)
│  │  ├─ planner.py           #   make_planner (LCEL + 구조화 출력)
│  │  ├─ prompts.py           #   PLANNER_PROMPT
│  │  └─ schemas.py           #   ChangeRequest, ParsedSR
│  ├─ policy/                 # 변경 정책 (코드 강제)
│  │  ├─ rules.py             #   PK, WRITABLE, WRITABLE_DESC, MAX_AFFECTED
│  │  ├─ precheck.py          #   작업 종류·화이트리스트·허용 코드값 사전검사
│  │  └─ sql_guard.py         #   check_sql (sqlglot), sql_literal, set_values
│  ├─ drafting/               # 대상 분석·SQL 작성·검토 (Supervisor + 3 Agent)
│  │  ├─ supervisor.py        #   build_change_supervisor, supervisor_input
│  │  ├─ submit_tools.py      #   submit_targets / submit_sql / get_review_packet / submit_review
│  │  └─ prompts.py           #   ANALYST / SQL / REVIEWER / SUPERVISOR 프롬프트
│  ├─ execution/              # 검증·실행 (ops-exec 호출은 여기서만)
│  │  ├─ dry_run_gate.py      #   rollback_statements, verify_with_dry_run
│  │  └─ executor.py          #   execute_items (실행·사후 검증·부분 실행분 원복)
│  ├─ history/store.py        # 변경 이력 Store: HISTORY_NS, history_warnings, record_changes
│  ├─ reporting/templates.py  # 승인 카드·리포트 고정 문구, guard_output(출력 가드)
│  ├─ tools/                  # MCP 도구 계층
│  │  ├─ ops_readonly_server.py  # ops-readonly MCP 서버 (describe_table, query_readonly, count_affected)
│  │  ├─ ops_exec_server.py      # ops-exec MCP 서버 (execute_sql[v1], dry_run, execute_change)
│  │  └─ client.py               # mcp_tools(db_path): 세션 유지형 도구 로더
│  ├─ baseline/               # v1 기준선 (단일 ReAct, 프롬프트 규칙만)
│  │  ├─ agent.py             #   make_tracer, run_request  ← v1 공통 인터페이스
│  │  └─ prompts.py           #   SYSTEM_PROMPT
│  └─ shared/                 # 여러 도메인이 함께 쓰는 것만
│     ├─ config.py            #   경로, MODEL_ID/REGION, make_llm()
│     ├─ middleware.py        #   하위 Agent 공통 미들웨어 (재시도, 도구 결과 PII 마스킹)
│     ├─ tracer.py            #   FileTracer (JSONL trace)
│     ├─ messages.py          #   get_text, tool_output_text
│     ├─ pii.py / db.py       #   PII 마스킹, SQLite 연결·해시
├─ eval/
│  ├─ inout_set.json / run_eval.py / llm_judge.py
│  └─ results_v1.json / results_v2.json
```

### 11.1 파일 배치 규칙

1. **모든 에이전트 코드는 `src/` 아래에 둔다.** 루트에는 진입점(`main.py`)만, 평가는 `eval/`, 데이터 생성은 `data/`에 둔다.
2. **폴더는 기술 역할이 아니라 SR 처리 도메인 단위로 나눈다.** 처리 순서: `intake` → `policy` → `drafting` → `execution` → `history` → `reporting`. 이 순서를 `workflows`가 연결한다. `utils/`, `models/`, `prompts/` 같은 기술 역할 폴더는 만들지 않는다.
3. **도메인 폴더 안에는 그 도메인의 실행 로직, 프롬프트(`prompts.py`), 입출력 스키마(`schemas.py`)를 함께 둔다.** 새 Agent나 단계가 생기면 해당 도메인 폴더에 로직, 프롬프트, 스키마를 같이 추가한다.
4. **`shared/`에는 두 개 이상 도메인이 공통으로 쓰고 도메인 지식이 없는 것만 둔다.** 해당하는 것은 LLM 클라이언트, 미들웨어, tracer, 텍스트·PII·DB 유틸이다. 정책 상수(화이트리스트·한도)는 `policy/rules.py`, 이력 네임스페이스는 `history/store.py`에 둔다(도메인 지식이므로 shared가 아니다).
5. **의존 방향**:
   - `workflows` → 각 도메인 → `policy` / `tools` / `shared`
   - `shared`는 다른 `src` 패키지를 import하지 않는다.
   - 도메인끼리는 스키마(`intake.schemas`, `workflows.schemas`)와 `policy`만 참조한다.
   - `workflows/schemas.py`는 `intake.schemas`만 import한다(순환 방지).
   - 패키지 `__init__.py`는 비워 둔다.
6. **안전 경계 (§3)**:
   - `ops-exec` 도구(`dry_run`, `execute_change`)는 `execution/`에서만 호출한다.
   - `drafting/`의 Agent에는 `ops-readonly` 도구와 제출 도구만 준다.
   - `baseline/`(v1)만 예외로 `execute_sql`을 LLM에 바인딩한다.
7. **MCP 서버 파일**(`tools/*_server.py`)은 자식 프로세스로 실행된다. 맨 위에서 `sys.path`에 프로젝트 루트(`parents[2]`)를 넣으므로, 이 파일을 옮기면 그 깊이도 함께 고친다. `shared/config.py`의 `ROOT`도 같다.
8. **공통 인터페이스(§7.1) 모듈 경로**: v1은 `src.baseline.agent`, v2는 `src.workflows.sr_graph`이다. 이 경로를 바꾸면 `eval/run_eval.py`의 `VERSIONS`와 `main.py`도 함께 고친다.

## 12. 진행 체크리스트

- [x] 설계 확정 (2026-11-12)
- [x] git init, `.env` 복사, venv·requirements
- [x] `data/make_db.py` — 샘플 DB (schema_hash=170b9de38ef7fc15, data_hash=6834ab10c7f3edb1, 3월 미정산 48건)
- [x] `eval/inout_set.json` — 케이스 19개 + 전역 검사 G08 (기대 diff를 초기 DB와 대조 완료)
- [x] `SERVICE.md` 골격 (§6 결과·실패 분석은 TODO)
- [x] P1: MCP 서버 2개 + v1 + run_eval → `results_v1.json` (15/20)
- [x] P2: v2 구현 (`src/workflows/`, `drafting/`, `policy/`, `intake/` 등) + `main.py`
- [x] P3: `results_v2.json` (20/20, Judge 5.0) + ablation + SERVICE.md §6 비교·실패 분석
- [ ] `main.py` CLI 실제 실행 확인 — 2026-11-12 Bedrock **일일 토큰 한도 소진**으로 미확인. 한도 복구 후 `--reset`으로 F01 요청 1건 시연 확인 필요
- [ ] (선택) ablation G03·G09 재실행 — 실행 B가 토큰 한도 오류로 미완

### 진행 중 알게 된 것
- 평가 1회 전체 실행 ≈ v1 32만 / v2 37만 토큰. 하루에 여러 번 돌리면 Bedrock 일일 토큰 한도에 걸린다
- Planner·Supervisor LLM은 미들웨어가 안 붙으므로 재시도는 `make_llm()`의 botocore adaptive retry가 담당
- v1 승인 응답은 "변경 제안(SQL 블록)마다 최대 3회" 규칙 (v1 사전 실행 후 보완, SERVICE.md §6.6에 기록)
