"""Long-lived MCP client over stdio (Python MCP server) in a background asyncio thread."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client


def _schema_to_dict(s: Any) -> dict[str, Any]:
    if s is None:
        return {"type": "object", "properties": {}}
    if isinstance(s, dict):
        return s
    if hasattr(s, "model_dump"):
        return s.model_dump(exclude_none=True)  # type: ignore[no-any-return]
    return {"type": "object", "properties": {}}


def mcp_tools_to_openai_list(tools: list[types.Tool]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tools:
        desc = (t.description or getattr(t, "title", None) or "") or ""
        schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None)
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": str(desc)[:80_000],
                    "parameters": _schema_to_dict(schema),
                },
            }
        )
    return out


class McpStdioClient:
    """One subprocess (MCP stdio server) and one ClientSession for the process lifetime."""

    def __init__(self, *, command: str, args: list[str], env: dict[str, str] | None) -> None:
        self._command = command
        self._args = list(args)
        self._env = dict(env) if env else None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: ClientSession | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._connect_exc: Exception | None = None

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._run())
        finally:
            try:
                loop.stop()
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None
            self._session = None

    async def _run(self) -> None:
        params = StdioServerParameters(command=self._command, args=self._args, env=self._env)
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    while not self._stop.is_set():
                        await asyncio.sleep(0.2)
        except Exception as e:  # noqa: BLE001
            self._connect_exc = e
            self._ready.set()
        finally:
            self._session = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and self._ready.is_set():
                if self._connect_exc is not None:
                    raise self._connect_exc
                if self._session is not None:
                    return
            if self._thread is None or not self._thread.is_alive():
                self._ready.clear()
                self._connect_exc = None
                self._stop.clear()
                self._thread = threading.Thread(target=self._thread_main, name="mcp-stdio", daemon=True)
                self._thread.start()
        if not self._ready.wait(timeout=120):
            raise RuntimeError("MCP stdio: timeout waiting for server to initialize")
        if self._connect_exc is not None:
            raise self._connect_exc
        if self._session is None:
            raise RuntimeError("MCP stdio: no session after init")

    def shutdown(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=8)
        self._thread = None
        self._session = None
        self._loop = None

    def _schedule(self, coro: Any, timeout: float) -> Any:
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    def list_tools(self, timeout_sec: float) -> list[dict[str, Any]]:
        self.start()
        if self._session is None or self._loop is None:
            raise RuntimeError("MCP stdio: session not available")

        async def _list() -> list[dict[str, Any]]:
            r = await self._session.list_tools()
            items = list(r.tools) if hasattr(r, "tools") else list(r)  # type: ignore[arg-type]
            return mcp_tools_to_openai_list(items)

        return self._schedule(_list(), timeout=timeout_sec + 10.0)

    def call_tool(self, name: str, arguments: dict[str, Any], timeout_sec: float) -> str:
        self.start()
        if self._session is None or self._loop is None:
            raise RuntimeError("MCP stdio: session not available")

        async def _call() -> str:
            result = await self._session.call_tool(name, arguments=arguments)
            parts: list[str] = []
            for block in result.content:
                if isinstance(block, types.TextContent):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            text = "\n".join(parts) if parts else ""
            if getattr(result, "isError", False):
                return json.dumps({"ok": False, "error": text, "mcp_isError": True}, ensure_ascii=False)
            return text

        return self._schedule(_call(), timeout=timeout_sec + 10.0)


_client: McpStdioClient | None = None
_client_key: tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]] | None = None


def _env_tuple(env: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not env:
        return ()
    return tuple(sorted(env.items()))


def get_stdio_client(command: str, args: list[str], env: dict[str, str] | None) -> McpStdioClient:
    global _client, _client_key
    key = (command.strip(), tuple(args), _env_tuple(env))
    with threading.Lock():
        if _client is not None and _client_key == key:
            return _client
        if _client is not None:
            try:
                _client.shutdown()
            except Exception:
                pass
        _client = McpStdioClient(command=command, args=args, env=env)
        _client_key = key
        return _client
