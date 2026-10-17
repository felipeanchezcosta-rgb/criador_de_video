"""Análise visual das imagens com o Groq (opcional).

Para gastar poucos tokens, 6 imagens viram UMA folha de contato numerada (cada imagem enviada
custa cerca de 2048 tokens, não importa o tamanho). O modelo diz onde está o assunto de cada
imagem, o clima e a energia da cena. O editor usa isso para enquadrar e cortar com sentido.

Se algo falhar (sem modelo de visão, limite do Groq), o editor usa a análise local, que não usa API.
Quase nada de RAM: a folha de contato tem uns 4 MB e é montada uma por vez.
"""
import base64
import io
import json
import os
from pathlib import Path

import groq
from groq import Groq
from PIL import Image, ImageDraw, ImageFont

import config
import groq_helper
from utils import load_json, log, save_json, sleep_with_progress, warn

ENABLED = os.getenv("VISION", "1") != "0"
MODEL = os.getenv("GROQ_VISION_MODEL", "").strip()
PAUSE = float(os.getenv("VISION_PAUSE_SECONDS", "35"))
MAX_TOKENS = int(os.getenv("VISION_MAX_TOKENS", "800"))
TILE, COLS, ROWS = 480, 3, 2
PER_SHEET = COLS * ROWS
MOODS = {"tense", "calm", "chaotic", "ominous", "mysterious"}
FLAGS = {"logo", "readable_text", "gibberish_text", "hand", "face", "blank_dark"}

SYSTEM = (
    "You are a film editor's assistant. You look at contact sheets of still images for a dark "
    "cinematic documentary and report, for each numbered tile, where the viewer's eye should go. "
    "Return ONLY valid JSON. Coordinates are fractions from 0 to 1 inside the tile square "
    "(x from the left, y from the top). Be honest about defects."
)

PROMPT = (
    "This is a contact sheet with {n} numbered tiles (yellow number in the top-left corner of each tile; "
    "a picture may have black bars inside its tile). For EACH tile return an entry:\n"
    '{{"tiles":[{{"id":1,"subject":"3 to 6 words","focus":[{{"x":0.5,"y":0.5}}],'
    '"mood":"tense|calm|chaotic|ominous|mysterious","energy":3,"flags":[]}}]}}\n'
    "focus = up to 3 points where the main subject or most interesting detail is, most important first. "
    "energy = 1 (still, slow) to 5 (violent, urgent). "
    "flags = any of: logo, readable_text, gibberish_text, hand, face, blank_dark (only if clearly present). "
    "Return exactly {n} entries, ids 1 to {n}."
)


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _make_sheet(paths):
    sheet = Image.new("RGB", (TILE * COLS, TILE * ROWS), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    font = _font(40)
    layout = []
    for i, path in enumerate(paths):
        with Image.open(path) as img:
            img = img.convert("RGB")
            scale = TILE / max(img.size)
            w, h = max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale))
            thumb = img.resize((w, h), Image.LANCZOS)
        ox, oy = (TILE - w) // 2, (TILE - h) // 2
        cx, cy = (i % COLS) * TILE, (i // COLS) * TILE
        sheet.paste(thumb, (cx + ox, cy + oy))
        draw.rectangle([cx, cy, cx + TILE - 1, cy + TILE - 1], outline=(255, 255, 255), width=3)
        draw.text((cx + 10, cy + 6), str(i + 1), fill=(255, 255, 0), font=font, stroke_width=3, stroke_fill=(0, 0, 0))
        layout.append((ox / TILE, oy / TILE, w / TILE, h / TILE))
    buffer = io.BytesIO()
    sheet.save(buffer, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode(), layout


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def _parse_tile(tile, layout):
    ox, oy, w, h = layout
    points = []
    for point in (tile.get("focus") or [])[:3]:
        try:
            x = _clamp((float(point["x"]) - ox) / w)
            y = _clamp((float(point["y"]) - oy) / h)
        except (KeyError, TypeError, ValueError):
            continue
        points.append([round(_clamp(x, 0.12, 0.88), 3), round(_clamp(y, 0.12, 0.88), 3)])
    if not points:
        return None
    mood = str(tile.get("mood", "")).lower()
    try:
        energy = int(round(float(tile.get("energy", 3))))
    except (TypeError, ValueError):
        energy = 3
    flags = [f for f in (tile.get("flags") or []) if f in FLAGS]
    return {
        "subject": str(tile.get("subject", ""))[:60],
        "focus": points,
        "mood": mood if mood in MOODS else "tense",
        "energy": max(1, min(5, energy)),
        "flags": flags,
    }


def _ask(client, model, paths):
    data_url, layout = _make_sheet(paths)
    text = PROMPT.format(n=len(paths))
    for attempt in range(1, 4):
        try:
            answer = groq_helper.chat_vision(client, model, SYSTEM, text, data_url, MAX_TOKENS)
            break
        except groq.RateLimitError as exc:
            if attempt == 3:
                raise
            try:
                wait = min(float(exc.response.headers.get("retry-after")), 300) + 1
            except Exception:
                wait = PAUSE * 2
            warn(f"Limite do Groq na análise visual. Aguardando {wait:.0f}s")
            sleep_with_progress(wait, "Aguardando limite do Groq")
    data = json.loads(answer[answer.find("{"): answer.rfind("}") + 1])
    results = {}
    for tile in data.get("tiles", []):
        try:
            index = int(tile["id"]) - 1
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= index < len(paths):
            parsed = _parse_tile(tile, layout[index])
            if parsed:
                results[index] = parsed
    return results


def analyze_images(image_paths, cache_path):
    """Devolve uma lista com um dict por imagem (ou None, se não deu para analisar)."""
    image_paths = [Path(p) for p in image_paths]
    results = [None] * len(image_paths)
    if not ENABLED:
        log("Análise visual desligada (VISION=0). Usando só a análise local.")
        return results

    cache = {}
    if Path(cache_path).exists():
        try:
            cache = load_json(cache_path)
        except Exception:
            cache = {}
    for i, path in enumerate(image_paths):
        if path.name in cache:
            results[i] = cache[path.name]

    pending = [i for i, r in enumerate(results) if r is None]
    if not pending:
        log("Análise visual: tudo já estava salvo.")
        return results

    try:
        client = Groq(api_key=config.GROQ_API_KEY)
        model = groq_helper.resolve_model(client, MODEL, groq_helper.PREFERRED_VISION, "visão")
    except Exception as exc:
        warn(f"Sem modelo de visão no Groq ({str(exc)[:120]}). Usando só a análise local.")
        return results

    batches = [pending[i: i + PER_SHEET] for i in range(0, len(pending), PER_SHEET)]
    log(f"Análise visual: {len(pending)} imagens em {len(batches)} lotes (uns {int(PAUSE)}s entre eles)")
    for number, batch in enumerate(batches, start=1):
        log(f"Análise visual: lote {number}/{len(batches)}")
        try:
            found = _ask(client, model, [image_paths[i] for i in batch])
        except Exception as exc:
            warn(f"Lote {number} falhou ({str(exc)[:150]}). Essas imagens usarão a análise local.")
            found = {}
        for position, i in enumerate(batch):
            if position in found:
                results[i] = found[position]
                cache[image_paths[i].name] = found[position]
        save_json(cache, cache_path)
        if number < len(batches):
            sleep_with_progress(PAUSE, "Pausa entre lotes de visão")

    flagged = [(i + 1, r["flags"]) for i, r in enumerate(results) if r and r["flags"]]
    for index, flags in flagged:
        warn(f"Imagem {index}: possível defeito ({', '.join(flags)})")
    done = sum(r is not None for r in results)
    log(f"Análise visual concluída: {done}/{len(results)} imagens com análise do Groq")
    return results
