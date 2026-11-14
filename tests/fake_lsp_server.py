"""A tiny LSP server used by tests.

Implements just enough of the protocol for the client's diagnostics, hover,
definition, references, and document-symbol requests.
"""

from __future__ import annotations

import json
import sys


def send(message: dict) -> None:
    body = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n")
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def read_message() -> dict | None:
    headers: dict[bytes, bytes] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        key, _, value = line.partition(b":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get(b"content-length", b"0") or 0)
    if length <= 0:
        return None
    return json.loads(sys.stdin.buffer.read(length))


def main() -> None:
    while True:
        message = read_message()
        if message is None:
            return
        method = message.get("method")
        request_id = message.get("id")

        if request_id is None:
            if method == "textDocument/didOpen":
                uri = message["params"]["textDocument"]["uri"]
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/publishDiagnostics",
                        "params": {
                            "uri": uri,
                            "diagnostics": [
                                {
                                    "range": {
                                        "start": {"line": 0, "character": 0},
                                        "end": {"line": 0, "character": 1},
                                    },
                                    "severity": 1,
                                    "message": "fake error",
                                }
                            ],
                        },
                    }
                )
            elif method == "exit":
                return
            continue

        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "capabilities": {
                            "hoverProvider": True,
                            "definitionProvider": True,
                            "referencesProvider": True,
                            "documentSymbolProvider": True,
                        }
                    },
                }
            )
        elif method == "textDocument/hover":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"contents": {"kind": "markdown", "value": "**hover text**"}},
                }
            )
        elif method == "textDocument/definition":
            uri = message["params"]["textDocument"]["uri"]
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": [
                        {
                            "uri": uri,
                            "range": {
                                "start": {"line": 4, "character": 2},
                                "end": {"line": 4, "character": 8},
                            },
                        }
                    ],
                }
            )
        elif method == "textDocument/references":
            uri = message["params"]["textDocument"]["uri"]
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": [
                        {
                            "uri": uri,
                            "range": {
                                "start": {"line": 1, "character": 0},
                                "end": {"line": 1, "character": 3},
                            },
                        }
                    ],
                }
            )
        elif method == "textDocument/documentSymbol":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": [
                        {
                            "name": "main",
                            "kind": 12,
                            "range": {
                                "start": {"line": 2, "character": 0},
                                "end": {"line": 3, "character": 0},
                            },
                        }
                    ],
                }
            )
        elif method == "shutdown":
            send({"jsonrpc": "2.0", "id": request_id, "result": None})
        else:
            send({"jsonrpc": "2.0", "id": request_id, "result": None})


if __name__ == "__main__":
    main()
