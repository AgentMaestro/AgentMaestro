from django.conf import settings

from .providers.openai_client import OpenAIClient
from .providers.ollama_client import OllamaClient

_CLIENTS = {}


def get_client(provider: str):
    provider_key = (provider or settings.LLM_PROVIDER).lower()
    if provider_key in _CLIENTS:
        return _CLIENTS[provider_key]
    if provider_key == "openai":
        client = OpenAIClient()
    elif provider_key == "ollama":
        client = OllamaClient()
    else:
        raise ValueError(f"Unknown LLM provider '{provider_key}'")
    _CLIENTS[provider_key] = client
    return client
