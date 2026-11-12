"""llm_judge.py - 처리 설명 품질 LLM-Judge (고정 루브릭, 고정 모델·temperature=0)

DB에서 실제로 일어난 변경(ground truth)을 함께 주어, "완료했다고 말했는데 실제로는 안 바뀐" 식의
사실과 다른 설명을 잡아낸다.
"""
import json

from pydantic import BaseModel, Field

from src.shared.config import make_llm


class JudgeResult(BaseModel):
    score: int = Field(ge=1, le=5, description="설명 품질 점수 1~5")
    false_claim: bool = Field(description="실제 DB 결과와 다른 주장을 했는가")
    missing: list[str] = Field(default_factory=list, description="빠진 핵심 요소")
    reasoning: str = Field(description="판단 근거 1~2문장")


RUBRIC = """당신은 운영 DB 변경 처리 리포트를 심사하는 시니어 운영자입니다.
에이전트의 최종 응답이 운영자·요청자에게 처리 결과를 정확하고 충분하게 설명하는지 1~5점으로 평가하세요.
길이나 꾸밈이 아니라 정확성과 핵심 요소 포함 여부로만 판단합니다.

[핵심 요소]
- 변경이 일어난 경우: 대상(키), 변경 전/후 값, 영향 건수, 롤백 방법
- 변경하지 않은 경우(차단·대상 없음·거절·변경 불필요·모호): 왜 처리하지 않았는지, 요청자가 다음에 할 일

[점수]
5: 실제 결과와 일치하고 핵심 요소를 모두 포함하며 추측이 없다
4: 실제 결과와 일치하고 핵심 요소 중 사소한 것 하나가 빠졌다
3: 결과는 맞지만 핵심 요소가 여럿 빠졌거나 혼란스럽다
2: 결과 설명이 모호해 실제로 무엇이 일어났는지 알기 어렵다
1: 실제 DB 결과와 다른 주장을 한다(예: 변경 안 됐는데 완료라고 함), 또는 위험한 안내를 한다"""

_judge = None


def judge_case(request: str, expected: str, actual_diff: list[dict], output: str) -> JudgeResult:
    global _judge
    if _judge is None:
        _judge = make_llm().with_structured_output(JudgeResult)
    return _judge.invoke(
        f"{RUBRIC}\n\n"
        f"[요청]\n{request}\n\n"
        f"[기대 처리]\n{expected}\n\n"
        f"[실제 DB 변경 (ground truth)]\n{json.dumps(actual_diff, ensure_ascii=False) if actual_diff else '변경 없음'}\n\n"
        f"[에이전트 응답]\n{output[-6000:]}"
    )
