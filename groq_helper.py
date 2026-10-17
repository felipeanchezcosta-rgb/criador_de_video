import re

import groq

from utils import log, warn

# Ordem de preferência. O primeiro que existir na sua conta é usado.
PREFERRED_STORY = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-20b",
    "qwen/qwen3-32b",
    "llama-3.3-70b-versatile",
]
PREFERRED_VISION = [
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
]
PREFERRED_FAST = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "llama-3.1-8b-instant",
]

_models_cache = {}
_resolved = {}
_features = {}  # modelo -> opções que a API aceitou


def available_models(client):
    if "ids" not in _models_cache:
        _models_cache["ids"] = sorted(m.id for m in client.models.list().data)
    return _models_cache["ids"]


def resolve_model(client, explicit, preferred, label):
    """Usa o modelo do .env se existir; senão escolhe o primeiro disponível na conta."""
    if explicit:
        return explicit
    if label in _resolved:
        return _resolved[label]
    ids = available_models(client)
    for name in preferred:
        if name in ids:
            _resolved[label] = name
            log(f"Modelo do Groq ({label}): {name}")
            return name
    raise RuntimeError(
        "Nenhum modelo conhecido está disponível na sua conta do Groq. "
        "Rode `python listar_modelos.py` e coloque um dos nomes no .env "
        "(GROQ_STORY_MODEL e GROQ_MODEL). Disponíveis: " + ", ".join(ids)
    )


def _reasoning_extras(model):
    if "gpt-oss" in model:
        return {"reasoning_effort": "low"}
    if "qwen" in model:
        return {"reasoning_effort": "none"}
    return {}


def _strip_thinking(text):
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def _chat(client, model, messages, max_tokens, temperature, feature_key):
    """Chama o Groq pedindo JSON. Se a API recusar alguma opção, tenta sem ela."""
    base = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    json_mode = {"response_format": {"type": "json_object"}}
    extras = _reasoning_extras(model)
    options = [{**extras, **json_mode}, {**json_mode}, {**extras}, {}]
    if feature_key in _features:
        options = [_features[feature_key]]

    last_error = None
    for option in options:
        try:
            response = client.chat.completions.create(**base, **option)
        except groq.BadRequestError as exc:
            last_error = exc
            continue
        _features[feature_key] = option
        text = _strip_thinking(response.choices[0].message.content)
        if not text:
            raise ValueError(
                "Resposta vazia: o modelo gastou os tokens pensando. "
                "Aumente STORY_SECTION_MAX_TOKENS / IMAGE_PROMPT_MAX_TOKENS / VISION_MAX_TOKENS no .env."
            )
        return text
    warn("O Groq recusou todas as combinações de opções para este modelo.")
    raise last_error


def chat_text(client, model, system, user, max_tokens, temperature=0.7):
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return _chat(client, model, messages, max_tokens, temperature, model)


def chat_vision(client, model, system, user_text, image_data_url, max_tokens, temperature=0.2):
    """Pergunta sobre uma imagem (data URL base64). Cada imagem custa cerca de 2048 tokens de entrada."""
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        },
    ]
    return _chat(client, model, messages, max_tokens, temperature, model + "|vision")
