"""runtime.py - MCP 세션 연결, 메시지 텍스트 추출 등 v1/v2 공용 런타임 유틸"""
import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

SRC = Path(__file__).resolve().parent
SERVERS = {
    "ops-readonly": SRC / "mcp_readonly.py",
    "ops-exec": SRC / "mcp_exec.py",
}


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


@asynccontextmanager
async def mcp_tools(db_path: str, servers=("ops-readonly", "ops-exec")):
    """서버별 세션을 열어 둔 채 도구를 로드한다 -> {server_name: {tool_name: tool}}

    get_tools()는 도구 호출마다 서버 프로세스를 새로 띄우므로 세션을 유지하는 방식을 쓴다.
    DB 경로는 환경변수 OPS_DB_PATH 로 서버에 전달한다.
    """
    env = {**os.environ, "OPS_DB_PATH": str(db_path), "PYTHONIOENCODING": "utf-8",
           "FASTMCP_LOG_LEVEL": "WARNING", "PYTHONWARNINGS": "ignore"}
    client = MultiServerMCPClient({
        name: {"command": sys.executable, "args": [str(SERVERS[name])], "transport": "stdio", "env": env}
        for name in servers
    })
    async with AsyncExitStack() as stack:
        loaded = {}
        for name in servers:
            session = await stack.enter_async_context(client.session(name))
            tools = await load_mcp_tools(session)
            loaded[name] = {t.name: t for t in tools}
        yield loaded
