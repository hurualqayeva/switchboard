"""Prompt token estimation for when a provider does not report usage.

tiktoken's cl100k_base is exact for OpenAI models and within a few percent for the other
families, which is good enough for routing decisions and for filling a ledger gap.
Provider-reported usage always wins when present.
"""

from functools import lru_cache

import tiktoken

from switchboard.schemas.openai import ChatMessage

TOKENS_PER_MESSAGE = 4  # role and framing overhead, matches OpenAI's published heuristic


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_text_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_encoding().encode(text, disallowed_special=()))


def estimate_prompt_tokens(messages: list[ChatMessage]) -> int:
    total = 0
    for m in messages:
        total += TOKENS_PER_MESSAGE + count_text_tokens(m.text())
        for tc in m.tool_calls or []:
            total += count_text_tokens(tc.function.name) + count_text_tokens(tc.function.arguments)
    return total + 2  # assistant priming
