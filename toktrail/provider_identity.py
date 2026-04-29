from __future__ import annotations


def inferred_provider_from_model(model_id: str) -> str | None:
    model = model_id.strip().lower()
    if not model or model == "unknown":
        return None

    if model.startswith("claude") or "anthropic" in model:
        return "anthropic"
    if (
        model.startswith("gpt-")
        or model.startswith("gpt")
        or model.startswith("o1")
        or model.startswith("o3")
        or model.startswith("o4")
        or "openai" in model
    ):
        return "openai"
    if model.startswith("gemini") or "google" in model:
        return "google"
    if model.startswith("llama") or "meta" in model:
        return "meta"
    if model.startswith("mistral") or model.startswith("codestral"):
        return "mistral"
    if model.startswith("deepseek"):
        return "deepseek"
    if model.startswith("grok") or model.startswith("xai"):
        return "xai"
    if model.startswith("raptor") or model.startswith("goldeneye"):
        return "github"

    return None
