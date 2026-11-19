"""Permission rules and approval flow.

Rules are evaluated last-match-wins, mirroring OpenCode: every rule whose action
and resource patterns match is considered, and the final rule decides. The
default when nothing matches is ``ask``.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class PermissionRule:
    action: str
    resource: str
    effect: str  # allow | deny | ask

    def matches(self, action: str, resource: str) -> bool:
        return _match(action, self.action) and _match(resource, self.resource)

    def as_dict(self) -> dict[str, str]:
        return {"action": self.action, "resource": self.resource, "effect": self.effect}

    def __str__(self) -> str:
        return f"{self.action}:{self.resource}:{self.effect}"


def _match(value: str, pattern: str) -> bool:
    if pattern == "*":
        return True
    return fnmatch.fnmatchcase(value, pattern)


@dataclass
class PermissionRequest:
    action: str
    resources: list[str]
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    session_id: str | None = None

    def summary(self) -> str:
        joined = ", ".join(self.resources[:6])
        if len(self.resources) > 6:
            joined += f" (+{len(self.resources) - 6} more)"
        return f"{self.action}: {joined}"


@dataclass
class PermissionDecision:
    effect: str
    rule: PermissionRule | None = None
    reason: str = ""


class PermissionDenied(Exception):
    """Raised when a tool call is denied by policy or the user."""


# Rules that keep an "auto" agent useful but stop the obvious footguns.
DANGEROUS_BASH_RULES: list[PermissionRule] = [
    # Generic asks first; specific denies last so they win (last match wins).
    PermissionRule("bash", "*sudo *", "ask"),
    PermissionRule("bash", "*shutdown*", "ask"),
    PermissionRule("bash", "*reboot*", "ask"),
    PermissionRule("bash", "*rm -rf *", "ask"),
    PermissionRule("bash", "*git push*", "ask"),
    PermissionRule("bash", "*git reset --hard*", "ask"),
    PermissionRule("bash", "*git clean -*", "ask"),
    PermissionRule("bash", "*curl *|*sh*", "ask"),
    PermissionRule("bash", "*wget *|*sh*", "ask"),
    PermissionRule("bash", "*rm -rf /*", "deny"),
    PermissionRule("bash", "*mkfs*", "deny"),
    PermissionRule("bash", "*dd if=/dev/*", "deny"),
    PermissionRule("bash", "*:(){*", "deny"),
    PermissionRule("bash", "*> /dev/sd*", "deny"),
    PermissionRule("bash", "*chmod -R 777 /*", "deny"),
]


def default_rules(mode: str = "auto") -> list[PermissionRule]:
    rules = [
        PermissionRule("read", "*", "allow"),
        PermissionRule("edit", "*", "allow"),
        PermissionRule("bash", "*", "allow"),
        PermissionRule("webfetch", "*", "allow"),
        PermissionRule("web_search", "*", "allow"),
        PermissionRule("mcp", "*", "allow"),
    ]
    normalized = (mode or "auto").lower()
    if normalized == "readonly":
        rules.extend(
            [
                PermissionRule("edit", "*", "deny"),
                PermissionRule("bash", "*", "deny"),
                PermissionRule("webfetch", "*", "deny"),
                PermissionRule("web_search", "*", "deny"),
                PermissionRule("mcp", "*", "deny"),
            ]
        )
    elif normalized == "ask":
        rules.extend(
            [
                PermissionRule("edit", "*", "ask"),
                PermissionRule("bash", "*", "ask"),
                PermissionRule("webfetch", "*", "ask"),
                PermissionRule("web_search", "*", "ask"),
            ]
        )
    else:  # auto
        rules.extend(DANGEROUS_BASH_RULES)
    return rules


def parse_rule(text: str) -> PermissionRule:
    """Parse ``action:resource:effect`` (resource may contain colons)."""
    parts = text.strip().split(":")
    if len(parts) < 3:
        raise ValueError(f"Invalid permission rule '{text}'. Expected action:resource:effect")
    action, effect = parts[0], parts[-1]
    resource = ":".join(parts[1:-1])
    if effect not in ("allow", "deny", "ask"):
        raise ValueError(f"Invalid effect '{effect}'. Use allow, deny, or ask")
    return PermissionRule(action.strip() or "*", resource.strip() or "*", effect)


ApprovalCallback = Callable[[PermissionRequest], Awaitable[str]]


class PermissionManager:
    def __init__(
        self,
        *,
        mode: str = "auto",
        rules: list[PermissionRule] | None = None,
        approval: ApprovalCallback | None = None,
        session_id: str | None = None,
    ) -> None:
        self.mode = mode
        self.base_rules = default_rules(mode)
        self.user_rules = list(rules or [])
        self.session_rules: list[PermissionRule] = []
        self.approval = approval
        self.session_id = session_id

    @property
    def rules(self) -> list[PermissionRule]:
        return [*self.base_rules, *self.user_rules, *self.session_rules]

    def evaluate(self, action: str, resource: str) -> PermissionDecision:
        matched = [rule for rule in self.rules if rule.matches(action, resource)]
        if matched:
            rule = matched[-1]
            return PermissionDecision(rule.effect, rule)
        return PermissionDecision("ask", None, "no matching rule")

    def evaluate_many(self, action: str, resources: list[str]) -> PermissionDecision:
        if not resources:
            return self.evaluate(action, "*")
        decisions = [self.evaluate(action, resource) for resource in resources]
        for decision in decisions:
            if decision.effect == "deny":
                return decision
        for decision in decisions:
            if decision.effect == "ask":
                return decision
        return decisions[-1]

    def add_rule(self, rule: PermissionRule, *, session: bool = False) -> None:
        target = self.session_rules if session else self.user_rules
        target.append(rule)

    def reset_session_rules(self) -> None:
        self.session_rules.clear()

    def describe(self) -> str:
        lines = [f"Permission mode: **{self.mode}**", "", "Rules (last match wins):"]
        for rule in self.rules:
            lines.append(f"- `{rule}`")
        return "\n".join(lines)

    async def authorize(
        self,
        action: str,
        resources: list[str],
        *,
        tool: str = "",
        args: dict[str, Any] | None = None,
        reason: str = "",
    ) -> PermissionDecision:
        decision = self.evaluate_many(action, resources)
        if decision.effect == "allow":
            return decision
        if decision.effect == "deny":
            rule = f" by rule `{decision.rule}`" if decision.rule else ""
            raise PermissionDenied(f"Permission denied{rule}: {action} {', '.join(resources)}")

        request = PermissionRequest(
            action=action,
            resources=resources,
            tool=tool,
            args=args or {},
            reason=reason,
            session_id=self.session_id,
        )
        if self.approval is None:
            raise PermissionDenied(
                f"Permission required for {request.summary()} but no approval channel is available"
            )
        reply = await self.approval(request)
        if reply == "always":
            for resource in resources:
                self.add_rule(PermissionRule(action, resource, "allow"), session=True)
            return PermissionDecision("allow", reason="approved for session")
        if reply == "allow":
            return PermissionDecision("allow", reason="approved once")
        raise PermissionDenied(f"User denied {request.summary()}")
