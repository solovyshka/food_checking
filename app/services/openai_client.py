"""Shared OpenAI-compatible client helpers (OpenAI, OpenRouter, …)."""

from __future__ import annotations

from app.config import get_settings


def openai_auth_headers(*, json_content: bool = True) -> dict[str, str]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    headers: dict[str, str] = {
        "Authorization": f"Bearer {settings.openai_api_key}",
    }
    if json_content:
        headers["Content-Type"] = "application/json"
    # OpenRouter optional ranking metadata
    if settings.openai_http_referer:
        headers["HTTP-Referer"] = settings.openai_http_referer
    if settings.openai_app_title:
        headers["X-Title"] = settings.openai_app_title
    return headers


def uses_openai_official() -> bool:
    base = get_settings().openai_base_url.lower()
    return "api.openai.com" in base


def provider_label() -> str:
    base = get_settings().openai_base_url.lower()
    if "openrouter.ai" in base:
        return "OpenRouter"
    if "api.openai.com" in base:
        return "OpenAI"
    return "API"
