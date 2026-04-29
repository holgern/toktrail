from __future__ import annotations

from toktrail.provider_identity import inferred_provider_from_model


def test_inferred_provider_from_model_known_models() -> None:
    assert inferred_provider_from_model("claude-sonnet-4") == "anthropic"
    assert inferred_provider_from_model("gpt-5.4-mini") == "openai"
    assert inferred_provider_from_model("gemini-2.5-pro") == "google"
    assert inferred_provider_from_model("grok-code-fast-1") == "xai"
    assert inferred_provider_from_model("raptor-mini") == "github"


def test_inferred_provider_from_model_unknown() -> None:
    assert inferred_provider_from_model("custom-model") is None
