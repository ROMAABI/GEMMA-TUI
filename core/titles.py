from __future__ import annotations

from core.client import LlamaClient

_TITLE_SYSTEM = (
    "You generate concise chat-session titles. Reply with only the title, "
    "at most five words, plain text, no quotes, no punctuation at the end."
)


def _title_prompt(first_message: str) -> str:
    snippet = first_message.replace("\n", " ").strip()[:400]
    return (
        f"<start_of_turn>system\n{_TITLE_SYSTEM}<end_of_turn>\n"
        f"<start_of_turn>user\nName this chat session.\n"
        f"First user message:\n{snippet}<end_of_turn>\n"
        "<start_of_turn>model\n"
    )


def _clean_title(title: str) -> str:
    title = title.strip().strip('"').strip("'").strip()
    if title.endswith("."):
        title = title[:-1].rstrip()
    return title[:60]


async def suggest_title(client: LlamaClient, first_message: str) -> str:
    """Ask the local model to summarize a chat into a short session title."""
    raw = await client.complete(_title_prompt(first_message), n_predict=40, temperature=0.3)
    return _clean_title(raw)