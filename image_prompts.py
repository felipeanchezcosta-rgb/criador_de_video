"""Prompts de imagem guiados pela história.

1) O Groq lê a história e decide o "visual brief": tom (dark / mixed / bright), época, paleta e
   uma linha de estilo. História sombria -> imagens sombrias; história leve ou de sucesso ->
   imagens coloridas e vivas.
2) Cada cena recebe um prompt que mostra literalmente o que a fala diz, na época certa, com
   ângulos de câmera variados, e que ajusta o clima da própria cena dentro do tom geral.
"""
import json
import os
from pathlib import Path

from groq import Groq, RateLimitError

import config
import groq_helper
import themes
from utils import chunked, load_json, log, retry, save_json, sleep_with_progress, warn

GROQ_MODEL = os.getenv("GROQ_MODEL", "").strip()
BATCH_SIZE = int(os.getenv("IMAGE_PROMPT_BATCH", "10"))
MAX_TOKENS = int(os.getenv("IMAGE_PROMPT_MAX_TOKENS", "3000"))
STYLE_MAX_TOKENS = int(os.getenv("IMAGE_STYLE_MAX_TOKENS", "1200"))
MAX_429 = 6
TONES = {"dark", "mixed", "bright"}

STYLE_SYSTEM = (
    "You are the art director of a YouTube documentary channel about computer history, hacking, scams and "
    "tech-company disasters. Read the story and choose the visual look that MATCHES it.\n"
    "- tone 'dark': crimes, attacks, deaths, paranoia, collapses. Low-key lighting and deep shadows but ONE clearly lit "
    "focal point, cool teal/blue with occasional red, film grain.\n"
    "- tone 'mixed': suspense with moments of wonder or irony. Dark base with vivid colored accents (amber, cyan, "
    "magenta) and a few brighter scenes.\n"
    "- tone 'bright': playful, inventive, humorous, nostalgic or success stories. Saturated lively colors, warm light or "
    "colorful neon, energetic stylized illustration.\n"
    "Also give the era, so objects match the period (for example '2001 bedroom, CRT monitors, beige PCs, dial-up'); old "
    "stories must never look modern.\n"
    'Return ONLY JSON: {"tone":"dark|mixed|bright","era":"...","palette":"...","style_line":"comma separated keywords '
    "for an image model, max 45 words, including the tone, palette, era, 'stylized cinematic digital illustration' and "
    "'faceless, no text, no letters, no logos, no watermark'\"}"
)

PROMPT_SYSTEM = (
    "You write image generation prompts for a YouTube documentary about computer history, hacking, scams and tech "
    "disasters. VISUAL BRIEF: tone={tone}; era={era}; palette={palette}. "
    "Write every prompt in English only. Each prompt: 40 to 70 words, one clear subject, composition, lighting and mood. "
    "Show what the narration literally says: its place, period and objects. If the narration is abstract, use a concrete "
    "symbolic object tied to the topic. Follow the brief's tone, but let each scene's own emotion adjust it: ominous or "
    "violent moments darker and more contrasty, hopeful, funny or triumphant moments lighter and more colorful. "
    "Make it lively and cinematic: vary camera angle and distance between consecutive scenes (wide establishing shot, "
    "close-up of an object, low angle, overhead, silhouette against a window). "
    "Never show: logos, app icons, social media interfaces, chat bubbles, signs, posters, screens with readable words, "
    "brand names, hands, fingers or faces of any person. Show people only as faceless silhouettes seen from behind or far "
    "away. For company or app names show a generic symbolic object. Every image needs ONE clearly lit focal subject. "
    'Return ONLY JSON: {{"prompts":[{{"id":1,"prompt":"..."}}]}}'
)


def _wait_time(exc):
    try:
        return min(float(exc.response.headers.get("retry-after")), 300) + 1
    except Exception:
        return 30.0


def _call(client, model, system, user, max_tokens, temperature=0.7):
    """Chama o Groq e só espera quando ele manda (429), em vez de pausar entre todos os lotes."""
    for attempt in range(1, MAX_429 + 1):
        try:
            return groq_helper.chat_text(client, model, system, user, max_tokens, temperature=temperature)
        except RateLimitError as exc:
            if attempt == MAX_429:
                raise
            wait = _wait_time(exc)
            warn(f"Limite do Groq. Aguardando {wait:.0f}s ({attempt}/{MAX_429})")
            sleep_with_progress(wait, "Aguardando limite do Groq")


def _model(client):
    return groq_helper.resolve_model(client, GROQ_MODEL, groq_helper.PREFERRED_FAST, "prompts")


def _fallback_style():
    return {"tone": "dark", "era": "", "palette": "teal and crimson", "style_line": themes.VISUAL_STYLE}


def build_style(client, story, cache_path=None):
    if cache_path and Path(cache_path).exists():
        try:
            return load_json(cache_path)
        except Exception:
            pass
    scenes = story["scenes"]
    step = max(1, len(scenes) // 14)
    sample = " ".join(s["narration"] for s in scenes[::step])[:3000]
    user = f"TITLE: {story['title']}\nSTORY SAMPLE: {sample}"
    try:
        text = _call(client, _model(client), STYLE_SYSTEM, user, STYLE_MAX_TOKENS, temperature=0.4)
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        tone = str(data.get("tone", "dark")).lower()
        style = {
            "tone": tone if tone in TONES else "dark",
            "era": str(data.get("era", "")).strip()[:200],
            "palette": str(data.get("palette", "")).strip()[:200],
            "style_line": str(data.get("style_line", "")).strip()[:500],
        }
        if not style["style_line"]:
            raise ValueError("style_line vazio")
    except Exception as exc:
        warn(f"Groq não definiu o estilo visual ({str(exc)[:120]}); usando o estilo escuro padrão")
        style = _fallback_style()
    if cache_path:
        save_json(style, cache_path)
    log(f"Estilo visual: {style['tone']} | {style['era'] or 'sem época definida'} | {style['palette']}")
    return style


@retry(times=3, delay=10)
def _ask(client, batch, style):
    scenes_txt = "\n".join(
        f"{s['id']}. NARRATION: {s['narration']}\n   VISUAL IDEA: {s['visual']}" for s in batch
    )
    system = PROMPT_SYSTEM.format(
        tone=style["tone"], era=style.get("era") or "not specified", palette=style.get("palette") or "free"
    )
    text = _call(client, _model(client), system, f"Write one prompt per scene:\n{scenes_txt}", MAX_TOKENS)
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    return {int(p["id"]): str(p["prompt"]).strip() for p in data.get("prompts", [])}


def generate_prompts(story, cache_path=None):
    scenes = story["scenes"]
    client = Groq(api_key=config.GROQ_API_KEY)
    style_path = Path(cache_path).with_name("style.json") if cache_path else None
    style = build_style(client, story, style_path)

    results = {}
    batches = list(chunked(scenes, BATCH_SIZE))
    for index, batch in enumerate(batches, start=1):
        log(f"Prompts de imagem: lote {index}/{len(batches)}")
        try:
            results.update(_ask(client, batch, style))
        except Exception as exc:
            warn(f"Lote {index} falhou ({exc}); usando descrição visual do roteiro")

    prompts = []
    for scene in scenes:
        base = results.get(scene["id"]) or scene["visual"]
        prompts.append(f"{base}, {style['style_line']}"[:1900])
    if cache_path:
        save_json(prompts, cache_path)
    return prompts
