"""A tiny MCP stdio server used by tests.

Speaks newline-delimited JSON-RPC 2.0 and exposes two tools:
`echo` (returns its input) and `fail` (returns an error result).
"""

from __future__ import annotations

import json
import sys


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:
            continue
        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake", "version": "1.0"},
                    },
                }
            )
        elif method == "tools/list":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "Echo the supplied text",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                    "required": ["text"],
                                },
                            },
                            {
                                "name": "fail",
                                "description": "Always returns an error result",
                                "inputSchema": {"type": "object", "properties": {}},
                            },
                        ]
                    },
                }
            )
        elif method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "echo":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [
                                {"type": "text", "text": "echo: " + str(arguments.get("text", ""))}
                            ]
                        },
                    }
                )
            elif name == "fail":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [{"type": "text", "text": "boom"}],
                            "isError": True,
                        },
                    }
                )
            else:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": "unknown tool"},
                    }
                )
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "unknown method"},
                }
            )


if __name__ == "__main__":
    main()
