"""Thin client for the official Razorpay MCP server (test mode).

This is the ONLY module in the control plane allowed to talk to Razorpay.
Phase 0 has no mandate/policy gate in front of it yet — Phase 2 adds that.
"""

import json
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from config import get_settings


class RazorpayToolError(RuntimeError):
    """Raised when a Razorpay MCP tool call returns isError."""

    def __init__(self, tool: str, message: str):
        super().__init__(f"{tool} failed: {message}")
        self.tool = tool


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
