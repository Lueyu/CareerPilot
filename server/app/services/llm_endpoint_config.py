"""Resolve a model key together with its provider and base URL."""


def endpoint(config):
    return config["provider"], config["base_url"].rstrip("/")


def resolve_llm_config(env, overrides=None):
    defaults = {
        "provider": env.llm_provider or "",
        "base_url": env.llm_base_url or "",
        "model": env.llm_model or "",
        "api_key": (env.llm_api_key or "").strip(),
    }
    stored = {
        field: value for field, value in overrides.items()
        if field in defaults and isinstance(value, str) and value.strip()
    } if isinstance(overrides, dict) else {}
    config = {**defaults, **stored}
    if endpoint(config) != endpoint(defaults):
        # A different endpoint must have its own complete credential tuple.
        if not all(field in stored for field in ("provider", "base_url", "api_key")):
            config["api_key"] = ""
    if config["provider"] and config["api_key"]:
        config["source"] = "db" if isinstance(overrides, dict) else "env"
    else:
        config["source"] = "none"
    return config
