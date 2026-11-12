"""tools/client.py - ops-readonly / ops-exec MCP 서버에 세션을 열고 도구를 로드한다 (v1·v2 공용)"""
import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

HERE = Path(__file__).resolve().parent
SERVERS = {
    "ops-readonly": HERE / "ops_readonly_server.py",
    "ops-exec": HERE / "ops_exec_server.py",
}


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
