from switchboard.core.tokens import count_text_tokens, estimate_prompt_tokens
from switchboard.schemas.openai import ChatMessage, FunctionCall, ToolCall


def test_count_text_tokens() -> None:
    assert count_text_tokens("") == 0
    assert 1 <= count_text_tokens("hello") <= 2
    assert count_text_tokens("hello world, this is a sentence") > count_text_tokens("hello")


def test_estimate_prompt_tokens_includes_overhead_and_tool_calls() -> None:
    msgs = [
        ChatMessage(role="system", content="be brief"),
        ChatMessage(
            role="assistant",
            tool_calls=[
                ToolCall(id="c1", function=FunctionCall(name="lookup", arguments='{"q": "x"}'))
            ],
        ),
    ]
    n = estimate_prompt_tokens(msgs)
    assert n > 2 * 4 + 2
