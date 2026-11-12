"""shared/pii.py - 전화번호·이메일 PII 탐지·마스킹 (출력 가드, 로그 미들웨어, tracer 공용)"""
import re

PHONE_RE = re.compile(r"(01[016789])-?(\d{3,4})-?(\d{4})")
EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def _mask_email(m: re.Match) -> str:
    local = m.group(1)
    return f"{local[:2]}***@{m.group(2)}"


def mask_pii(text: str) -> str:
    """010-1234-5678 -> 010-****-5678, user105.mall11@example.com -> us***@example.com"""
    if not text:
        return text
    text = PHONE_RE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", text)
    return EMAIL_RE.sub(_mask_email, text)


def mask_obj(obj):
    """dict/list/str 안의 모든 문자열을 재귀적으로 마스킹"""
    if isinstance(obj, str):
        return mask_pii(obj)
    if isinstance(obj, dict):
        return {k: mask_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask_obj(v) for v in obj]
    return obj
