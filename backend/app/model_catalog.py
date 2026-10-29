"""Supported text models; identifiers are unambiguous across providers."""

MODELS = {
    "gemini-3.5-flash-lite": {"provider": "gemini", "label": "Gemini Flash Lite", "reasoning": []},
    "gpt-6-luna": {
        "provider": "openai",
        "label": "GPT-6 Luna",
        "reasoning": ["none", "low", "medium", "high"],
    },
}


def model_info(model, provider=None):
    info = MODELS.get(model)
    if not info or (provider is not None and provider != info["provider"]):
        raise ValueError("Choose a supported model for this provider.")
    return info


def validate_options(model, provider=None, max_output_tokens=2048, reasoning_effort=None):
    info = model_info(model, provider)
    if type(max_output_tokens) is not int or not 64 <= max_output_tokens <= 4096:
        raise ValueError("Response limit must be between 64 and 4096 tokens.")
    if reasoning_effort is not None and reasoning_effort not in info["reasoning"]:
        raise ValueError("This reasoning setting is not supported by the selected model.")
    return info


def catalog(settings):
    from app.pricing import price_for

    return [
        {
            "id": model,
            **info,
            "configured": bool(settings.credentials(info["provider"]).get_secret_value()),
            "input_per_million": str(price_for(model).input_per_million),
            "output_per_million": str(price_for(model).output_per_million),
        }
        for model, info in MODELS.items()
    ]
