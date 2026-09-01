"""Thin client for the official Razorpay MCP server (test mode).

This is the ONLY module in the control plane allowed to talk to Razorpay.
Phase 0 has no mandate/policy gate in front of it yet — Phase 2 adds that.
"""

import json
from contextlib import asynccontextmanager
from typing import Any, NoReturn

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from config import get_settings


class RazorpayToolError(RuntimeError):
    """Raised when a Razorpay MCP tool call returns isError."""

    def __init__(self, tool: str, message: str):
        super().__init__(f"{tool} failed: {message}")
        self.tool = tool


def reraise_unwrapped(eg: BaseExceptionGroup) -> NoReturn:
    """`except*` always binds an ExceptionGroup, even when exactly one plain
    exception was raised (confirmed: `raise RazorpayToolError(...)` inside a
    try/except* block arrives here as a 1-element group). Re-raising the
    group as-is would turn every RazorpayToolError/MandateError into an
    ExceptionGroup for every caller using a plain `except SomeError:` --
    unwrap back to the original exception whenever there's exactly one, so
    existing `except RazorpayToolError` / `except MandateError` handlers
    upstream keep matching unchanged. A genuine multi-exception group (rare)
    is re-raised as-is; callers fall back to a generic 500/502.
    """
    if len(eg.exceptions) == 1 and not isinstance(eg.exceptions[0], BaseExceptionGroup):
        raise eg.exceptions[0] from None
    raise eg


def _parse_tool_result(tool: str, result: Any) -> dict:
    if result.is_error:
        text = "; ".join(
            getattr(block, "text", str(block)) for block in result.content
        )
        raise RazorpayToolError(tool, text)
    if result.structured_content is not None:
        return result.structured_content
    for block in result.content:
        if hasattr(block, "text"):
            return json.loads(block.text)
    raise RazorpayToolError(tool, "empty response content")


@asynccontextmanager
async def razorpay_session():
    """Open one MCP session against the Razorpay server for the duration of
    a single request. Short-lived and single-purpose by construction — a new
    session is opened per call, nothing is held open across requests.
    """
    settings = get_settings()
    headers = {"Authorization": f"Basic {settings.razorpay_merchant_token}"}
    async with create_mcp_http_client(headers=headers) as http_client:
        async with streamable_http_client(
            settings.razorpay_mcp_url, http_client=http_client
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def call_tool(session: ClientSession, tool: str, arguments: dict) -> dict:
    result = await session.call_tool(tool, arguments)
    return _parse_tool_result(tool, result)
