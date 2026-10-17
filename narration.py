import io
import os
import re
import shutil
from pathlib import Path

import requests

import config
from utils import log, make_progress, media_duration, retry

ELEVEN_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "onyx")
KOKORO_RATE = 24000

_kokoro_cache = {}


def clean_for_tts(text):
    text = re.sub(r"[*_#`]+", "", text)
    text = text.replace("&", " and ")
    return re.sub(r"\s+", " ", text).strip()


def _kokoro_pipeline():
    if "pipeline" not in _kokoro_cache:
        from kokoro import KPipeline

        voice = config.KOKORO_VOICE
        lang = voice[0] if voice[:1] in ("a", "b") else "a"
        log(f"Carregando Kokoro (voz {voice}). Na primeira vez baixa o modelo.")
        _kokoro_cache["pipeline"] = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M")
    return _kokoro_cache["pipeline"]


def _kokoro(text):
    import numpy as np
    import soundfile as sf

    pipeline = _kokoro_pipeline()
    parts = []
    for _, _, audio in pipeline(text, voice=config.KOKORO_VOICE, speed=config.KOKORO_SPEED):
        if audio is None:
            continue
        if hasattr(audio, "detach"):
            audio = audio.detach().cpu().numpy()
        parts.append(np.asarray(audio, dtype="float32"))
    if not parts:
        raise RuntimeError("Kokoro não devolveu áudio para o texto.")
    buffer = io.BytesIO()
    sf.write(buffer, np.concatenate(parts), KOKORO_RATE, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


ONNX_DIR = config.BASE_DIR / "assets" / "models"
ONNX_FILES = {
    "kokoro-v1.0.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}
_onnx_cache = {}


def _download(url, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log(f"Baixando {dest.name} (só na primeira vez)...")
    with requests.get(url, stream=True, timeout=60, allow_redirects=True) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as out:
            shutil.copyfileobj(resp.raw, out, length=1024 * 1024)
    os.replace(tmp, dest)


def _kokoro_onnx_engine():
    if "engine" not in _onnx_cache:
        from kokoro_onnx import Kokoro

        for name, url in ONNX_FILES.items():
            path = ONNX_DIR / name
            if not path.exists() or path.stat().st_size < 1_000_000:
                _download(url, path)
        log(f"Carregando Kokoro leve (voz {config.KOKORO_VOICE})")
        _onnx_cache["engine"] = Kokoro(str(ONNX_DIR / "kokoro-v1.0.onnx"), str(ONNX_DIR / "voices-v1.0.bin"))
    return _onnx_cache["engine"]


def _kokoro_onnx(text):
    import numpy as np
    import soundfile as sf

    engine = _kokoro_onnx_engine()
    voice = config.KOKORO_VOICE
    lang = "en-gb" if voice[:1] == "b" else "en-us"
    samples, rate = engine.create(text, voice=voice, speed=config.KOKORO_SPEED, lang=lang)
    buffer = io.BytesIO()
    sf.write(buffer, np.asarray(samples, dtype="float32"), rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


@retry(times=3, delay=5)
def _elevenlabs(text):
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}",
        params={"output_format": "mp3_44100_128"},
        headers={
            "xi-api-key": config.ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": ELEVEN_MODEL,
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.75,
                "style": 0.25,
                "use_speaker_boost": True,
            },
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.content


@retry(times=3, delay=5)
def _openai(text):
    payload = {
        "model": OPENAI_TTS_MODEL,
        "voice": OPENAI_TTS_VOICE,
        "input": text,
        "response_format": "mp3",
    }
    if OPENAI_TTS_MODEL.startswith("gpt-4o"):
        payload["instructions"] = (
            "Deep, calm, suspenseful documentary narrator. Clear American English, steady pace."
        )
    response = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    return response.content


PROVIDERS = {"kokoro_onnx": _kokoro_onnx, "kokoro": _kokoro, "elevenlabs": _elevenlabs, "openai": _openai}
EXTENSIONS = {"kokoro_onnx": ".wav", "kokoro": ".wav", "elevenlabs": ".mp3", "openai": ".mp3"}


def generate_narration(scenes, audio_dir):
    name = config.NARRATION_PROVIDER
    provider = PROVIDERS.get(name)
    if provider is None:
        raise ValueError(f"NARRATION_PROVIDER inválido: {name}. Use: {', '.join(PROVIDERS)}")
    audio_dir = Path(audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with make_progress() as progress:
        task = progress.add_task(f"Narração ({name})", total=len(scenes))
        for scene in scenes:
            path = audio_dir / f"scene_{scene['id']:03d}{EXTENSIONS[name]}"
            if not path.exists() or path.stat().st_size == 0:
                path.write_bytes(provider(clean_for_tts(scene["narration"])))
            results.append({"path": str(path), "duration": media_duration(path)})
            progress.advance(task)
    total = sum(r["duration"] for r in results)
    log(f"Narração pronta: {total:.1f}s de áudio")
    return results
