from dataclasses import dataclass


@dataclass(frozen=True)
class SessionSummary:
    id: str
    title: str
    updated_at: str
