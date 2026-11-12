"""intake/input_guard.py - 입력 가드: SR 원문의 SQL 조각·지시 무시·승인 우회·허위 보고 요구를 정규식으로 차단"""
import re
from dataclasses import dataclass

INPUT_RULES = [
    ("SQL 인젝션 구문", re.compile(r";\s*(drop|delete|update|insert|alter|truncate|create|exec)\b", re.I)),
    ("SQL 주석 기호", re.compile(r"(--|/\*|\*/)")),
    ("SQL 항진식", re.compile(r"'\s*or\s*'?\w*'?\s*=\s*'?\w*", re.I)),
    ("UNION SELECT 구문", re.compile(r"\bunion\s+(all\s+)?select\b", re.I)),
    ("DDL 구문", re.compile(r"\b(drop|truncate|alter)\s+table\b", re.I)),
    ("지시 무시 요구", re.compile(r"(이전|앞의|위의|기존|모든)\s*(지시|명령|규칙|지침)[^.\n]{0,10}(무시|잊)")),
    ("지시 무시 요구", re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules)", re.I)),
    ("승인 절차 우회 요구", re.compile(r"승인\s*(절차|단계|과정)?\s*(없이|생략|건너|패스|무시)")),
    ("WHERE 조건 생략 요구", re.compile(r"where\s*(조건|절)?\s*(없이|빼고|생략)", re.I)),
    ("처리 결과 허위 보고 요구", re.compile(r"(보고서?|리포트|결과)[^.\n]{0,10}(\d+\s*건)?[^.\n]{0,5}(이?라고|로)\s*(쓰|적|기재|보고)")),
]


@dataclass
class InputVerdict:
    blocked: bool
    rule: str = ""
    message: str = ""


def check_input(text: str) -> InputVerdict:
    for name, pattern in INPUT_RULES:
        if pattern.search(text):
            return InputVerdict(True, name, (
                f"해당 요청은 처리할 수 없습니다: 요청 본문에 '{name}'이(가) 포함되어 있습니다. "
                "변경 대상(키)·변경할 항목·변경 값·사유만 담아 다시 요청해 주세요. 변경 0건."))
    return InputVerdict(False)
