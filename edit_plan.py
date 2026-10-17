import math
import os
import random
from pathlib import Path

import numpy as np
from PIL import Image

import config
from utils import format_seconds, log, save_json

FPS = config.VIDEO_FPS
SCENE_PAUSE = 0.30
FINAL_PAUSE = 1.5
# ~1 corte a cada 4 segundos, com movimento lento e contínuo
SHOT_MIN = float(os.getenv("SHOT_MIN_SECONDS", "3.4"))
SHOT_MAX = float(os.getenv("SHOT_MAX_SECONDS", "4.6"))
SHAKE_CHANCE = float(os.getenv("SHAKE_CHANCE", "0"))
GRID = 48
ENERGY_SCALE = {1: 1.15, 2: 1.08, 3: 1.0, 4: 0.95, 5: 0.9}
KEY_GAP = 8  # no mínimo 8 cenas entre dois momentos-chave com flash/glitch
SECTION_DIP = 0.5  # segundos de fade nas viradas de seção

EFFECTS = [
    "zoom_in",
    "zoom_out",
    "pan_left",
    "pan_right",
    "pan_up",
    "pan_down",
    "push_in",
    "pull_out",
]
# o movimento acompanha o clima da cena (vision.json)
MOOD_EFFECTS = {
    "calm": ["zoom_in", "pan_left", "pan_right", "zoom_out"],
    "tense": ["push_in", "zoom_in", "pan_left", "pan_right", "pan_up"],
    "ominous": ["zoom_in", "pull_out", "pan_down", "push_in"],
    "mysterious": ["pan_left", "pan_right", "pull_out", "pan_up"],
    "chaotic": ["push_in", "zoom_in", "pan_down", "pan_up"],
}
# força do movimento (bem pequena = lento e discreto)
STRENGTH_RANGE = (0.05, 0.09)
STRENGTH_MOOD = {"calm": 0.8, "tense": 1.0, "ominous": 0.9, "mysterious": 0.9, "chaotic": 1.2}
FRAME_WIDTHS = [1.0, 0.88, 0.78]


# ---------- análise local da imagem (sem API, sem tokens) ----------
def analyze_image(path):
    """Mapa de interesse (onde está o assunto) + brilho médio da imagem."""
    with Image.open(path) as img:
        gray = np.asarray(img.convert("L").resize((GRID + 1, GRID + 1)), dtype=np.float32) / 255.0
        ratio = img.size[0] / img.size[1]
    gx = np.abs(np.diff(gray, axis=1))[:-1, :]
    gy = np.abs(np.diff(gray, axis=0))[:, :-1]
    energy = gx + gy
    energy = energy / (energy.max() + 1e-6)
    body = gray[:-1, :-1]
    interest = 0.65 * energy + 0.35 * body
    return {"interest": interest, "mean": float(body.mean()), "ratio": ratio}


def _gamma_for(mean):
    if mean >= 0.20:
        return 1.0
    return round(min(1.0 + (0.20 - mean) * 5.0, 1.6), 2)


def _window_scores(interest, gw, gh):
    padded = np.pad(interest, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    h, w = interest.shape
    rows, cols = h - gh + 1, w - gw + 1
    scores = np.zeros((rows, cols), dtype=np.float32)
    for y in range(rows):
        top, bottom = padded[y], padded[y + gh]
        scores[y] = (bottom[gw:gw + cols] - bottom[:cols] - top[gw:gw + cols] + top[:cols]) / (gw * gh)
    return scores


def _pick_crop(analysis, rng, fw, previous):
    interest = analysis["interest"]
    size = interest.shape[0]
    ratio = analysis["ratio"]
    out_ratio = config.VIDEO_WIDTH / config.VIDEO_HEIGHT
    fh = min(fw * ratio / out_ratio, 1.0)
    gw, gh = max(2, int(round(fw * size))), max(2, int(round(fh * size)))
    gw, gh = min(gw, size), min(gh, size)
    scores = _window_scores(interest, gw, gh)

    flat = scores.ravel()
    order = np.argsort(flat)[::-1]
    candidates = []
    for idx in order[: max(12, len(order) // 6)]:
        y, x = divmod(int(idx), scores.shape[1])
        fx = x / max(size - gw, 1)
        fy = y / max(size - gh, 1)
        if previous is not None and abs(fx - previous[0]) + abs(fy - previous[1]) + abs(fw - previous[2]) < 0.35:
            continue
        candidates.append((fx, fy, x, y))
        if len(candidates) >= 6:
            break
    if not candidates:
        y, x = divmod(int(order[0]), scores.shape[1])
        candidates = [(x / max(size - gw, 1), y / max(size - gh, 1), x, y)]
    fx, fy, x, y = rng.choice(candidates)

    window = interest[y:y + gh, x:x + gw]
    total = float(window.sum()) + 1e-6
    ys, xs = np.mgrid[0:gh, 0:gw]
    cx = float((window * xs).sum() / total) / max(gw - 1, 1)
    cy = float((window * ys).sum() / total) / max(gh - 1, 1)
    return [round(fx, 3), round(fy, 3), fw], [round(min(max(cx, 0.15), 0.85), 3), round(min(max(cy, 0.15), 0.85), 3)]


def _vision_crop(analysis, rng, fw, previous, points):
    """Enquadra em volta de um ponto que o Groq indicou como assunto da imagem."""
    ratio = analysis["ratio"]
    out_ratio = config.VIDEO_WIDTH / config.VIDEO_HEIGHT
    fh = min(fw * ratio / out_ratio, 1.0)
    options = list(points)
    rng.shuffle(options)
    best = None
    for px, py in options:
        fx = 0.5 if fw >= 0.999 else min(max((px - fw / 2) / (1 - fw), 0.0), 1.0)
        fy = 0.5 if fh >= 0.999 else min(max((py - fh / 2) / (1 - fh), 0.0), 1.0)
        best = ([round(fx, 3), round(fy, 3), fw], [px, py])
        if previous is None or abs(fx - previous[0]) + abs(fy - previous[1]) + abs(fw - previous[2]) >= 0.2:
            return best
    return best


def _split_frames(total, rng, scale=1.0):
    """Divide a cena em cortes de duração parecida (~4s), sem sobras longas no fim."""
    target = (SHOT_MIN + SHOT_MAX) / 2 * FPS * scale
    count = max(1, int(round(total / target)))
    base, extra = divmod(total, count)
    sizes = [base + (1 if i < extra else 0) for i in range(count)]
    jitter = int(base * 0.1)
    if jitter > 0:
        for i in range(count - 1):
            shift = rng.randint(-jitter, jitter)
            sizes[i] += shift
            sizes[i + 1] -= shift
    return [max(size, 8) for size in sizes]


def build_plan(story, image_paths, narrations, music_path, project_dir, vision=None):
    project_dir = Path(project_dir)
    rng = random.Random(story["title"])
    scenes = []
    previous_effect = None
    previous_section = None
    previous_last_shot = None
    last_key = -KEY_GAP
    total_frames = 0
    count = len(story["scenes"])
    dip = int(FPS * SECTION_DIP)

    for index, scene in enumerate(story["scenes"]):
        analysis = analyze_image(image_paths[index])
        gamma = _gamma_for(analysis["mean"])
        is_last = index == count - 1
        extra_pause = float(scene.get("pause", 0) or 0)
        pause = FINAL_PAUSE if is_last else SCENE_PAUSE + extra_pause
        frames = math.ceil((narrations[index]["duration"] + pause) * FPS)
        info = vision[index] if vision and index < len(vision) else None
        energy = info["energy"] if info else 3
        mood = info["mood"] if info else "tense"

        # momentos-chave: só eles ganham flash e glitch
        impact = bool(scene.get("impact"))
        key = impact or (energy >= 5 and index - last_key >= KEY_GAP)
        if key:
            last_key = index

        section = scene.get("section", 0)
        section_change = index > 0 and section != previous_section
        if section_change and previous_last_shot is not None:
            previous_last_shot["fade_out"] = dip
        previous_section = section

        scale = ENERGY_SCALE.get(energy, 1.0) * (1.5 if is_last else 1.0)
        sizes = _split_frames(frames, rng, scale)
        strength_mood = STRENGTH_MOOD.get(mood, 1.0)
        shots = []
        previous_crop = None
        for shot_index, size in enumerate(sizes):
            first = shot_index == 0
            fw = 1.0 if first else rng.choice(FRAME_WIDTHS)
            if info and rng.random() < 0.7:
                crop, focus = _vision_crop(analysis, rng, fw, previous_crop, info["focus"])
            else:
                crop, focus = _pick_crop(analysis, rng, fw, previous_crop)
            options = [e for e in MOOD_EFFECTS.get(mood, EFFECTS) if e != previous_effect] or EFFECTS
            effect = rng.choice(options)

            fade_in = 0
            if index == 0 and first:
                fade_in = int(FPS * 0.8)
            elif first and section_change:
                fade_in = dip
            shots.append(
                {
                    "frames": size,
                    "effect": effect,
                    "crop": crop,
                    "focus": focus,
                    "gamma": gamma,
                    "strength": round(rng.uniform(*STRENGTH_RANGE) * strength_mood, 3),
                    "flash": bool(first and key and not fade_in),
                    "glitch": bool(first and key and (impact or mood == "chaotic") and index > 0),
                    "shake": rng.random() < SHAKE_CHANCE,
                    "grain": rng.random() < 0.25,
                    "fade_in": fade_in,
                    "fade_out": int(FPS * 1.2) if is_last and shot_index == len(sizes) - 1 else 0,
                }
            )
            previous_effect, previous_crop = effect, crop
        previous_last_shot = shots[-1]
        scenes.append(
            {
                "id": scene["id"],
                "image": str(image_paths[index]),
                "audio": narrations[index]["path"],
                "text": scene["narration"],
                "speech": round(narrations[index]["duration"], 4),
                "label": scene.get("label", ""),
                "section": section,
                "impact": impact,
                "key": key,
                "mood": mood,
                "energy": energy,
                "frames": frames,
                "duration": round(frames / FPS, 4),
                "shots": shots,
            }
        )
        total_frames += frames

    plan = {
        "title": story["title"],
        "resolution": [config.VIDEO_WIDTH, config.VIDEO_HEIGHT],
        "fps": FPS,
        "total_frames": total_frames,
        "total_duration": round(total_frames / FPS, 3),
        # com ducking (a música abaixa quando há fala) dá para deixar a base um pouco mais alta
        "music": {"path": music_path, "volume": round(config.MUSIC_VOLUME * 1.8, 3)},
        "scenes": scenes,
        "output": str(project_dir / "final.mp4"),
    }
    save_json(plan, project_dir / "edit_plan.json")
    shot_count = sum(len(s["shots"]) for s in scenes)
    keys = sum(1 for s in scenes if s["key"])
    log(
        f"Plano de edição: {len(scenes)} imagens, {shot_count} cortes "
        f"(um corte a cada {plan['total_duration'] / max(shot_count, 1):.1f}s), "
        f"{keys} momentos-chave, {format_seconds(plan['total_duration'])}"
    )
    return plan
