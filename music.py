import json
import os
import random
import wave

import numpy as np

import config
import groq_helper
from utils import log, warn

SR = 22050
ASSETS_MUSIC = config.BASE_DIR / "assets" / "music"
AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".ogg", ".flac")
MODEL = os.getenv("GROQ_MODEL", "").strip()
AMBIENT_SECONDS = 90


def _smooth_noise(rng, n, window=400):
    cumulative = np.cumsum(rng.normal(0, 1, n + window))
    out = (cumulative[window:] - cumulative[:-window]) / window
    return out / (out.std() + 1e-9)


def _write_wav(path, data):
    pcm = (np.clip(data, -1, 1) * 32767).astype(np.int16).T.copy()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(SR)
        wav.writeframes(pcm.tobytes())


def generate_ambient(duration, path, seed=None):
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    t = np.arange(n) / SR
    root = random.choice([55.0, 49.0, 61.74, 65.41])
    partials = [(1.0, 0.50), (1.5, 0.25), (2.0, 0.18), (3.01, 0.08)]
    signal = np.zeros((2, n))
    for channel in range(2):
        detune = 1 + 0.002 * (channel * 2 - 1)
        for ratio, amp in partials:
            lfo_rate = rng.uniform(0.03, 0.10)
            lfo = 0.5 + 0.5 * np.sin(2 * np.pi * lfo_rate * t + rng.uniform(0, 6.28))
            phase = rng.uniform(0, 6.28)
            signal[channel] += amp * lfo * np.sin(2 * np.pi * root * ratio * detune * t + phase)
        signal[channel] += 0.05 * _smooth_noise(rng, n)
    signal /= np.abs(signal).max() / 0.8
    fade = min(int(3 * SR), n // 2)
    ramp = np.linspace(0, 1, fade)
    signal[:, :fade] *= ramp
    signal[:, -fade:] *= ramp[::-1]
    _write_wav(path, signal)
    return str(path)


def _list_tracks():
    if not ASSETS_MUSIC.exists():
        return []
    return sorted(p for p in ASSETS_MUSIC.iterdir() if p.suffix.lower() in AUDIO_EXTENSIONS)


def _story_brief(story):
    opening = " ".join(s["narration"] for s in story["scenes"][:4])
    return f"TITLE: {story['title']}\nOPENING: {opening[:600]}"


def _choose_with_groq(tracks, story):
    from groq import Groq

    names = [t.name for t in tracks]
    client = Groq(api_key=config.GROQ_API_KEY)
    model = groq_helper.resolve_model(client, MODEL, groq_helper.PREFERRED_FAST, "trilha")
    system = (
        "You pick background music for a dark tech-crime documentary. "
        "Choose the ONE file whose name best fits the mood of the story. "
        'Return ONLY JSON: {"track": "<exact file name from the list>"}'
    )
    user = f"{_story_brief(story)}\n\nFILES:\n" + "\n".join(names)
    text = groq_helper.chat_text(client, model, system, user, 600, temperature=0.2)
    chosen = json.loads(text[text.find("{"): text.rfind("}") + 1]).get("track")
    for track in tracks:
        if track.name == chosen:
            return track
    return None


def get_music(duration, project_dir, story=None):
    tracks = _list_tracks()
    if tracks:
        chosen = None
        if story is not None:
            try:
                chosen = _choose_with_groq(tracks, story)
            except Exception as exc:
                warn(f"Groq não escolheu a trilha ({exc}); escolhendo ao acaso")
        chosen = chosen or random.choice(tracks)
        log(f"Música: {chosen.name}")
        return str(chosen)
    path = project_dir / "music.wav"
    if not path.exists():
        log("Pasta assets/music vazia: gerando trilha ambiente sintética")
        generate_ambient(min(duration + 2, AMBIENT_SECONDS), path)
    return str(path)
