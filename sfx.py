"""Efeitos sonoros sintéticos, um diferente por cena.

Sem baixar nada: cada efeito é gerado com numpy. O tipo é escolhido pelo clima da cena
(vision.json), por momentos de impacto e por virada de seção. Nenhum tipo se repete nas cenas
seguintes, e cada cena usa uma semente própria (altura, duração e timbre mudam).
"""
import wave
import zlib
from pathlib import Path

import numpy as np

from utils import log, save_json

SR = 22050


def _n(seconds):
    return int(seconds * SR)


def _t(n):
    return np.arange(n) / SR


def _smooth(x, w):
    if w <= 1 or len(x) <= w:
        return x
    c = np.cumsum(np.insert(x, 0, 0.0))
    out = (c[w:] - c[:-w]) / w
    return np.concatenate([np.full(w - 1, out[0]), out])


def _norm(x):
    return x / (np.abs(x).max() + 1e-9)


def whoosh(rng):
    n = _n(rng.uniform(0.9, 1.7))
    noise = rng.normal(size=n)
    lo = _norm(_smooth(noise, int(rng.integers(40, 90))))
    hi = _norm(_smooth(noise, int(rng.integers(3, 8))))
    r = np.linspace(0, 1, n)
    return (lo * (1 - r) + hi * r) * np.sin(np.pi * r) ** 2


def hit(rng):
    n = _n(1.6)
    t = _t(n)
    f = rng.uniform(32, 46) + 95 * np.exp(-t * rng.uniform(14, 22))
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * rng.uniform(2.6, 3.8))
    return body + 0.35 * rng.normal(size=n) * np.exp(-t * 45)


def riser(rng):
    d = rng.uniform(2.0, 3.2)
    n = _n(d)
    t = _t(n)
    r = t / d
    f = rng.uniform(180, 320) * (rng.uniform(4, 6) ** r)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.5
    noise = _norm(_smooth(rng.normal(size=n), int(rng.integers(6, 14)))) * 0.6
    out = (tone + noise) * r ** 2
    tail = int(0.03 * SR)
    out[-tail:] *= np.linspace(1, 0, tail)
    return out


def reverse(rng):
    n = _n(rng.uniform(1.6, 2.6))
    t = _t(n)
    noise = _norm(_smooth(rng.normal(size=n), int(rng.integers(10, 30))))
    tail = noise * np.exp(-t * rng.uniform(2.2, 3.4))
    sub = np.sin(2 * np.pi * rng.uniform(45, 70) * t) * np.exp(-t * 2.0) * 0.6
    return (tail + sub)[::-1].copy()


def drone(rng):
    d = rng.uniform(3.0, 4.5)
    n = _n(d)
    t = _t(n)
    f0 = float(rng.choice([55.0, 49.0, 61.74, 65.41, 73.42]))
    out = np.zeros(n)
    for ratio, amp in [(1, 1.0), (1.5, 0.5), (2.01, 0.35), (3.0, 0.15)]:
        out += amp * np.sin(2 * np.pi * f0 * ratio * t + rng.uniform(0, 6.28))
    return out * np.sin(np.pi * t / d) ** 2


def heartbeat(rng):
    gap = rng.uniform(0.85, 1.1)
    beats = int(rng.integers(3, 5))
    n = _n(gap * beats + 0.8)
    out = np.zeros(n)
    f = rng.uniform(48, 62)
    for b in range(beats):
        for off, amp in ((0.0, 1.0), (0.27, 0.7)):
            s, m = _n(b * gap + off), _n(0.3)
            if s + m > n:
                continue
            tt = _t(m)
            out[s:s + m] += amp * np.sin(2 * np.pi * f * tt) * np.exp(-tt * 16)
    return out


def static(rng):
    d = rng.uniform(1.6, 2.6)
    n = _n(d)
    t = _t(n)
    crackle = (rng.random(n) < rng.uniform(0.004, 0.012)) * rng.normal(size=n) * 3
    hiss = _smooth(rng.normal(size=n), 2) * 0.15
    return (crackle + hiss) * np.clip(np.sin(np.pi * t / d), 0, None) ** 0.5


def blips(rng):
    parts = []
    for _ in range(int(rng.integers(5, 10))):
        m = _n(rng.uniform(0.04, 0.09))
        f = float(rng.choice([880, 1175, 1480, 1760, 2093, 2637])) * rng.uniform(0.97, 1.03)
        parts.append(np.sin(2 * np.pi * f * _t(m)) * np.hanning(m))
        parts.append(np.zeros(_n(rng.uniform(0.05, 0.2))))
    return np.concatenate(parts)


def glitch(rng):
    parts = []
    for _ in range(int(rng.integers(6, 11))):
        m = _n(rng.uniform(0.02, 0.08))
        if rng.random() < 0.5:
            seg = rng.normal(size=m)
        else:
            seg = np.sign(np.sin(2 * np.pi * rng.uniform(200, 3000) * _t(m)))
        levels = int(rng.integers(3, 8))
        seg = np.round(seg * levels) / levels
        parts.append(seg * np.hanning(m))
        parts.append(np.zeros(_n(rng.uniform(0.0, 0.06))))
    return np.concatenate(parts)


def alarm(rng):
    pairs = [(880, 660), (990, 740), (760, 570)]
    fa, fb = pairs[int(rng.integers(len(pairs)))]
    parts = []
    for i in range(int(rng.integers(4, 7))):
        m = _n(0.16)
        f = fa if i % 2 == 0 else fb
        wave_ = np.sign(np.sin(2 * np.pi * f * _t(m)))
        parts.append(_smooth(wave_, 4) * np.hanning(m))
        parts.append(np.zeros(_n(0.04)))
    return np.concatenate(parts)


def ticks(rng):
    gap = rng.uniform(0.35, 0.6)
    k = int(rng.integers(5, 9))
    out = np.zeros(_n(gap * k + 0.3))
    for i in range(k):
        s, m = _n(i * gap), _n(0.05)
        tt = _t(m)
        out[s:s + m] += (rng.normal(size=m) * 0.5 + np.sin(2 * np.pi * rng.uniform(1500, 2200) * tt)) * np.exp(-tt * 120)
    return out


def keyboard(rng):
    out = np.zeros(_n(3.2))
    pos = 0.0
    while pos < 2.8:
        s, m = _n(pos), _n(0.03)
        tt = _t(m)
        out[s:s + m] += rng.uniform(0.4, 1.0) * _smooth(rng.normal(size=m), 2) * np.exp(-tt * 140)
        pos += rng.uniform(0.05, 0.22)
    return out


def chime(rng):
    n = _n(3.2)
    t = _t(n)
    f = rng.uniform(300, 700)
    out = np.zeros(n)
    for i, (ratio, amp) in enumerate([(1, 1.0), (2.76, 0.5), (5.4, 0.3), (8.93, 0.15)]):
        out += amp * np.sin(2 * np.pi * f * ratio * t) * np.exp(-t * (1.0 + i * 0.9))
    ramp = int(0.005 * SR)
    out[:ramp] *= np.linspace(0, 1, ramp)
    return out


def dialup(rng):
    parts = []
    tones = [1200, 1800, 2100, 2250, 980, 1650]
    for _ in range(int(rng.integers(8, 14))):
        m = _n(rng.uniform(0.06, 0.22))
        a, b = rng.choice(tones, 2, replace=False)
        tt = _t(m)
        parts.append((np.sin(2 * np.pi * a * tt) + np.sin(2 * np.pi * b * tt)) * 0.5 * np.hanning(m))
    m = _n(rng.uniform(0.8, 1.4))
    parts.append(_smooth(rng.normal(size=m), 2) * np.hanning(m) * 0.6)
    return np.concatenate(parts)


def sub_drop(rng):
    n = _n(2.4)
    t = _t(n)
    f = 26 + rng.uniform(60, 95) * np.exp(-t * rng.uniform(1.0, 1.6))
    env = np.exp(-t * 1.3)
    ramp = _n(0.02)
    env[:ramp] *= np.linspace(0, 1, ramp)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env


# nome: (função, ganho, alinhar o fim do efeito com o fim da cena?)
KINDS = {
    "whoosh": (whoosh, 0.45, False),
    "hit": (hit, 1.0, False),
    "riser": (riser, 0.5, True),
    "reverse": (reverse, 0.55, True),
    "drone": (drone, 0.35, False),
    "heartbeat": (heartbeat, 0.6, False),
    "static": (static, 0.3, False),
    "blips": (blips, 0.22, False),
    "glitch": (glitch, 0.4, False),
    "alarm": (alarm, 0.22, False),
    "ticks": (ticks, 0.35, False),
    "keyboard": (keyboard, 0.3, False),
    "chime": (chime, 0.35, False),
    "dialup": (dialup, 0.2, False),
    "sub_drop": (sub_drop, 0.8, False),
}

MOOD_POOLS = {
    "calm": ["drone", "chime", "ticks", "static", "blips"],
    "tense": ["heartbeat", "ticks", "blips", "riser", "keyboard", "static"],
    "ominous": ["drone", "sub_drop", "chime", "reverse", "heartbeat"],
    "mysterious": ["chime", "blips", "static", "reverse", "dialup", "keyboard"],
    "chaotic": ["glitch", "alarm", "static", "whoosh", "riser", "dialup"],
}
IMPACT_POOL = ["hit", "sub_drop", "glitch"]
SECTION_POOL = ["whoosh", "reverse", "riser", "sub_drop"]


def _pick(rng, pool, history):
    window = min(5, len(pool) - 1)
    recent = history[-window:] if window > 0 else []
    options = [k for k in pool if k not in recent] or pool
    return options[int(rng.integers(len(options)))]


def _write_wav(path, data):
    pcm = (np.tanh(data) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SR)
        wav.writeframes(pcm.tobytes())


def build_track(plan, project_dir):
    """Monta um WAV com um efeito por cena, alinhado com o início de cada cena."""
    project_dir = Path(project_dir)
    mix = np.zeros(_n(plan["total_duration"] + 2), dtype=np.float32)
    history, events = [], []
    start = 0.0
    previous_section = None
    for index, scene in enumerate(plan["scenes"]):
        seed = zlib.crc32(f"{plan['title']}|{scene['id']}".encode())
        rng = np.random.default_rng(seed)
        mood = scene.get("mood", "tense")
        if scene.get("impact") or scene.get("key"):
            pool = IMPACT_POOL
        elif index > 0 and scene.get("section") != previous_section:
            pool = SECTION_POOL
        else:
            pool = MOOD_POOLS.get(mood, MOOD_POOLS["tense"])
        previous_section = scene.get("section")

        kind = _pick(rng, pool, history)
        history.append(kind)
        func, gain, align_end = KINDS[kind]
        signal = _norm(func(rng)) * gain
        if align_end:
            offset = max(start, start + scene["duration"] - len(signal) / SR)
        else:
            offset = start + rng.uniform(0.0, 0.25)
        s = _n(offset)
        e = min(len(mix), s + len(signal))
        if e > s:
            mix[s:e] += signal[: e - s].astype(np.float32)
        events.append({"scene": scene["id"], "kind": kind, "time": round(offset, 2)})
        start += scene["duration"]

    path = project_dir / "sfx.wav"
    _write_wav(path, mix)
    save_json(events, project_dir / "sfx_events.json")
    kinds = {}
    for ev in events:
        kinds[ev["kind"]] = kinds.get(ev["kind"], 0) + 1
    log("Efeitos sonoros: " + ", ".join(f"{k} x{v}" for k, v in sorted(kinds.items())))
    return str(path), events
