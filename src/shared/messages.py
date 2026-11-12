"""shared/messages.py - LLM 메시지·도구 결과에서 텍스트를 뽑는 공용 유틸"""


def get_text(message) -> str:
    """ChatBedrockConverse는 content를 블록 리스트로 주기도 하므로 텍스트만 모아 반환한다."""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content or ""


def tool_output_text(result) -> str:
    """MCP 도구 호출 결과(str, content 블록 리스트, ToolMessage)를 문자열로 정규화"""
    if hasattr(result, "content"):
        result = result.content
    if isinstance(result, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in result)
    return str(result)
