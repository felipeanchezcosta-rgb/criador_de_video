import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _get(name, default=""):
    return os.getenv(name, default).strip()


def _get_float(name, default):
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return float(default)


def _get_int(name, default):
    try:
        return int(float(_get(name, str(default))))
    except ValueError:
        return int(default)


# --- Chaves de API ---
GROQ_API_KEY = _get("GROQ_API_KEY")
CLOUDFLARE_API_TOKEN = _get("CLOUDFLARE_API_TOKEN")
CLOUDFLARE_ACCOUNT_ID = _get("CLOUDFLARE_ACCOUNT_ID")
TOGETHER_API_KEY = _get("TOGETHER_API_KEY")
POLLINATIONS_API_KEY = _get("POLLINATIONS_API_KEY")
OVH_API_KEY = _get("OVH_API_KEY")  # opcional: sem chave, a OVHcloud funciona anônima (2 imagens por minuto)
ELEVENLABS_API_KEY = _get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = _get("ELEVENLABS_VOICE_ID")
OPENAI_API_KEY = _get("OPENAI_API_KEY")

# --- Provedores ---
NARRATION_PROVIDER = _get("NARRATION_PROVIDER", "kokoro_onnx").lower()
IMAGE_PROVIDER = _get("IMAGE_PROVIDER", "cloudflare").lower()
_providers_raw = _get("IMAGE_PROVIDERS")
if _providers_raw:
    IMAGE_PROVIDERS = [p.strip().lower() for p in _providers_raw.split(",") if p.strip()]
else:
    # Sem lista no .env: usa o provedor principal, a OVHcloud (não precisa de chave) e,
    # se houver chave, o Pollinations de reserva.
    IMAGE_PROVIDERS = [IMAGE_PROVIDER]
    if "ovh" not in IMAGE_PROVIDERS:
        IMAGE_PROVIDERS.append("ovh")
    if POLLINATIONS_API_KEY and "pollinations" not in IMAGE_PROVIDERS:
        IMAGE_PROVIDERS.append("pollinations")
if not IMAGE_PROVIDERS:
    IMAGE_PROVIDERS = [IMAGE_PROVIDER]

# --- Narração local (Kokoro) ---
KOKORO_VOICE = _get("KOKORO_VOICE", "am_michael")
KOKORO_SPEED = _get_float("KOKORO_SPEED", 1.0)

# --- Pastas ---
OUTPUT_DIR = Path(_get("OUTPUT_DIR", "output"))
if not OUTPUT_DIR.is_absolute():
    OUTPUT_DIR = BASE_DIR / OUTPUT_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Groq ---
GROQ_BATCH_PAUSE_SECONDS = _get_float("GROQ_BATCH_PAUSE_SECONDS", 30)

# --- Vídeo ---
VIDEO_WIDTH = _get_int("VIDEO_WIDTH", 1920)
VIDEO_HEIGHT = _get_int("VIDEO_HEIGHT", 1080)
VIDEO_FPS = _get_int("VIDEO_FPS", 24)
MUSIC_VOLUME = _get_float("MUSIC_VOLUME", 0.10)
RENDER_WORKERS = max(1, _get_int("RENDER_WORKERS", max(1, (os.cpu_count() or 2) // 2)))

REQUIRED_BY_NARRATION = {
    "kokoro_onnx": [],
    "kokoro": [],
    "elevenlabs": ["ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"],
    "openai": ["OPENAI_API_KEY"],
}

REQUIRED_BY_IMAGES = {
    "cloudflare": ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"],
    "together": ["TOGETHER_API_KEY"],
    "pollinations": ["POLLINATIONS_API_KEY"],
    "ovh": [],  # funciona sem chave
}


def missing_keys():
    required = ["GROQ_API_KEY"]
    required += REQUIRED_BY_IMAGES.get(IMAGE_PROVIDERS[0], [])
    required += REQUIRED_BY_NARRATION.get(NARRATION_PROVIDER, [])
    return [k for k in required if not globals().get(k)]


def invalid_providers():
    problems = []
    if NARRATION_PROVIDER not in REQUIRED_BY_NARRATION:
        problems.append(
            f"NARRATION_PROVIDER inválido ({NARRATION_PROVIDER}). Use: {', '.join(REQUIRED_BY_NARRATION)}"
        )
    for name in IMAGE_PROVIDERS:
        if name not in REQUIRED_BY_IMAGES:
            problems.append(
                f"Provedor de imagem inválido ({name}). Use: {', '.join(REQUIRED_BY_IMAGES)}"
            )
    return problems


def usable_image_providers():
    """Provedores da lista que têm todas as chaves preenchidas, na ordem de uso."""
    usable = []
    for name in IMAGE_PROVIDERS:
        if name not in REQUIRED_BY_IMAGES:
            continue
        keys = REQUIRED_BY_IMAGES[name]
        if all(globals().get(k) for k in keys):
            usable.append(name)
    return usable
