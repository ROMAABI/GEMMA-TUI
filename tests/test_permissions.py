from __future__ import annotations

import asyncio

import pytest

from core.permissions import (
    PermissionDenied,
    PermissionManager,
    PermissionRule,
    default_rules,
    parse_rule,
)


def test_default_auto_policy() -> None:
    manager = PermissionManager(mode="auto")
    assert manager.evaluate("read", "src/app.py").effect == "allow"
    assert manager.evaluate("edit", "src/app.py").effect == "allow"
    assert manager.evaluate("bash", "ls -la").effect == "allow"
    assert manager.evaluate("bash", "rm -rf /").effect == "deny"
    assert manager.evaluate("bash", "sudo apt install x").effect == "ask"
    assert manager.evaluate("bash", "git push origin main").effect == "ask"


def test_readonly_policy() -> None:
    manager = PermissionManager(mode="readonly")
    assert manager.evaluate("read", "x").effect == "allow"
    assert manager.evaluate("edit", "x").effect == "deny"
    assert manager.evaluate("bash", "ls").effect == "deny"


def test_last_match_wins() -> None:
    manager = PermissionManager(
        mode="auto",
        rules=[PermissionRule("bash", "git push*", "allow")],
    )
    assert manager.evaluate("bash", "git push origin main").effect == "allow"


def test_parse_rule_with_colons_in_resource() -> None:
    rule = parse_rule("bash:docker run:*:ask")
    assert rule.action == "bash"
    assert rule.resource == "docker run:*"
    assert rule.effect == "ask"


def test_authorize_denies_without_callback() -> None:
    manager = PermissionManager(mode="ask")
    with pytest.raises(PermissionDenied):
        asyncio.run(manager.authorize("bash", ["ls"]))


def test_authorize_allow_once_and_always() -> None:
    calls: list[str] = []

    async def approve(request) -> str:
        calls.append(request.summary())
        return "allow"

    manager = PermissionManager(mode="ask", approval=approve)
    decision = asyncio.run(manager.authorize("bash", ["ls"], tool="bash"))
    assert decision.effect == "allow"
    assert calls

    async def always(request) -> str:
        return "always"

    manager = PermissionManager(mode="ask", approval=always)
    asyncio.run(manager.authorize("edit", ["src/app.py"], tool="edit"))
    assert manager.evaluate("edit", "src/app.py").effect == "allow"


def test_authorize_user_denies() -> None:
    async def deny(request) -> str:
        return "deny"

    manager = PermissionManager(mode="ask", approval=deny)
    with pytest.raises(PermissionDenied):
        asyncio.run(manager.authorize("bash", ["ls"]))


def test_default_rules_include_dangerous_commands() -> None:
    rules = default_rules("auto")
    assert any(rule.effect == "deny" for rule in rules)
    assert any(rule.effect == "ask" for rule in rules)
