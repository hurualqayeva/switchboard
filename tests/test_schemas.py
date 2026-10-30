from switchboard.schemas.errors import ModelNotFoundError, ProviderRateLimitedError
from switchboard.schemas.openai import (
    ChatCompletionRequest,
    ChatMessage,
    ContentPart,
    ImageUrl,
    Usage,
)


def test_message_text_flattens_parts() -> None:
    m = ChatMessage(
        role="user",
        content=[
            ContentPart(type="text", text="a "),
            ContentPart(type="image_url", image_url=ImageUrl(url="http://x/y.png")),
            ContentPart(type="text", text="b"),
        ],
    )
    assert m.text() == "a b"
    assert ChatMessage(role="assistant").text() == ""


def test_request_helpers() -> None:
    req = ChatCompletionRequest(
        model="m", messages=[ChatMessage(role="user", content="hi")], max_tokens=5, stop="END"
    )
    assert req.effective_max_tokens == 5
    assert req.stop_sequences == ["END"]
    req2 = ChatCompletionRequest(
        model="m",
        messages=[ChatMessage(role="user", content="hi")],
        max_completion_tokens=9,
        stop=["a", "b"],
    )
    assert req2.effective_max_tokens == 9
    assert req2.stop_sequences == ["a", "b"]


def test_unknown_fields_are_ignored() -> None:
    req = ChatCompletionRequest.model_validate(
        {"model": "m", "messages": [{"role": "user", "content": "x"}], "frequency_penalty": 0.5}
    )
    assert req.model == "m"


def test_usage_of() -> None:
    assert Usage.of(3, 4).total_tokens == 7


def test_error_envelope() -> None:
    exc = ModelNotFoundError("nope", param="model")
    assert exc.status_code == 404
    assert exc.envelope() == {
        "error": {
            "message": "nope",
            "type": "invalid_request_error",
            "param": "model",
            "code": "model_not_found",
        }
    }
    prov = ProviderRateLimitedError(
        "slow down", provider="openai_compat", deployment="d", upstream_status=429
    )
    assert prov.retryable
    assert prov.status_code == 429
    assert prov.upstream_status == 429
