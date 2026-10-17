import os
from pathlib import Path

from groq import Groq, RateLimitError

import groq_helper

import config
import script_cleaner
from utils import (
    extract_json,
    load_json,
    log,
    retry,
    save_json,
    sleep_with_progress,
    warn,
)

STORY_MODEL = os.getenv("GROQ_STORY_MODEL", "").strip()
PROGRESS_FILE = config.OUTPUT_DIR / "story_progress.json"
SECTION_WORDS = 300
# ~16 palavras por cena = cenas de ~6s, que viram 1 a 2 cortes de ~4s cada
SCENE_WORDS = int(os.getenv("SCENE_WORDS", "16"))
WORDS_PER_MINUTE = int(os.getenv("WORDS_PER_MINUTE", "150"))
PLAN_MAX_TOKENS = int(os.getenv("STORY_PLAN_MAX_TOKENS", "1500"))
SECTION_MAX_TOKENS = int(os.getenv("STORY_SECTION_MAX_TOKENS", "3200"))
MAX_429_RETRIES = 8
MAX_RETRY_AFTER = 300
MAX_SUMMARY_CHARS = 2400

SYSTEM = (
    "You write scripts for a dark cinematic YouTube channel about tech crime, "
    "for an American audience. Write in English only, with short sentences and "
    "a spoken narration rhythm. No markdown, emojis or stage directions. "
    "Use only publicly documented facts (court records, official reports, "
    "reputable journalism). Never invent quotes, numbers, dates or private "
    "thoughts; hedge uncertain details or leave them out. Never give "
    "instructions that help anyone carry out an attack. "
    "FACT RULES: keep the chronology exact and never imply that one event caused another unless the sources say so; "
    "when a claim comes only from the person involved, write 'according to him/her'; if you are not sure of a count "
    "(charges, victims, computers, dollars), say 'about' or leave it out; mention a person's medical or personal condition "
    "at most once, factually and respectfully, never as a joke or a punchline; treat death, suicide and victims with a sober "
    "tone, no graphic detail and no speculation. "
    "NARRATION ONLY: the narration text is read aloud exactly as written, so never put in it pause markers like '(pause)', "
    "section headings, timestamps, thumbnail ideas, titles, descriptions, tags, chapters, voice or production instructions. "
    "Visuals: one clear subject per scene that literally matches what the narration says (same place, period and objects; "
    "silhouettes, servers, night cities, symbolic objects). "
    "Never describe hands, faces, logos, app icons, chat bubbles, signs or screens with words. Return ONLY valid JSON."
)

ROLE_RULES = {
    "opening": (
        "Start with a strong hook in the first two sentences: say what is at stake and promise what the viewer will find "
        "out. Create tension. Never say welcome. Do not conclude the story."
    ),
    "development": (
        "Advance the story with new documented facts and rising tension. "
        "Do not repeat earlier sections. Do not conclude the story."
    ),
    "ending": (
        "Deliver the consequences and what changed, then one practical takeaway "
        "for viewers, then one brief line inviting them to subscribe. End cleanly with that line; write nothing after it."
    ),
}


def _pause_seconds():
    try:
        return float(os.getenv("STORY_PAUSE_SECONDS", "8"))
    except ValueError:
        return 8.0


def _pause():
    sleep_with_progress(_pause_seconds(), "Pausa entre seções")


def _retry_after(exc):
    try:
        value = float(exc.response.headers.get("retry-after"))
        return min(max(value, 1.0), MAX_RETRY_AFTER) + 1
    except Exception:
        return max(_pause_seconds(), 20.0)


def _chat(client, user, max_tokens):
    model = groq_helper.resolve_model(client, STORY_MODEL, groq_helper.PREFERRED_STORY, "história")
    for attempt in range(1, MAX_429_RETRIES + 1):
        try:
            return groq_helper.chat_text(client, model, SYSTEM, user, max_tokens, temperature=0.8)
        except RateLimitError as exc:
            if attempt == MAX_429_RETRIES:
                raise
            wait = _retry_after(exc)
            warn(f"Erro 429 do Groq. Aguardando {wait:.0f}s (tentativa {attempt}/{MAX_429_RETRIES})")
            sleep_with_progress(wait, "Aguardando limite do Groq")


@retry(times=3, delay=5)
def _json_call(client, user, max_tokens, validator):
    return validator(extract_json(_chat(client, user, max_tokens)))


def _roles(minutes):
    count = max(2, round(minutes * WORDS_PER_MINUTE / SECTION_WORDS))
    return ["opening"] + ["development"] * (count - 2) + ["ending"]


def _plan_prompt(theme, roles):
    cases = "\n".join(f"- {c}" for c in theme["cases"])
    order = "\n".join(f"{i}. {r}" for i, r in enumerate(roles, start=1))
    return f"""Plan one video script.

THEME: {theme['name']}
ANGLE: {theme['angle']}
Pick ONE of these cases (or another equally well documented one):
{cases}

The script has {len(roles)} sections, in this order:
{order}

Return JSON:
{{"title": "clickable but honest title under 70 characters",
"description": "YouTube description, 2 short paragraphs",
"tags": ["10 to 15 tags"],
"case": "one sentence naming the chosen case",
"outline": ["one sentence per section, exactly {len(roles)} items, in order"]}}"""


def _validate_plan(data, count):
    title = str(data.get("title", "")).strip()
    outline = [str(x).strip() for x in data.get("outline", []) if str(x).strip()]
    if not title or not outline:
        raise ValueError("Plano sem título ou outline")
    outline = (outline + ["Continue the story with new documented facts."] * count)[:count]
    return {
        "title": title[:100],
        "description": str(data.get("description", "")).strip(),
        "tags": [str(t).strip() for t in data.get("tags", []) if str(t).strip()],
        "case": str(data.get("case", "")).strip(),
        "outline": outline,
    }


def _summary_so_far(sections):
    lines = [f"Section {i}: {s['summary']}" for i, s in enumerate(sections, start=1)]
    while len(lines) > 1 and len("\n".join(lines)) > MAX_SUMMARY_CHARS:
        lines.pop(0)
    return "\n".join(lines)


def _section_prompt(plan, roles, index, sections):
    outline = "\n".join(f"{i}. {r}: {o}" for i, (r, o) in enumerate(zip(roles, plan["outline"]), start=1))
    if sections:
        so_far = _summary_so_far(sections)
        last = sections[-1]["scenes"][-1]["narration"]
        continuity = f'SO FAR:\n{so_far}\nLAST LINE WRITTEN: "{last}"'
    else:
        continuity = "SO FAR: nothing yet. This is the first section."
    role = roles[index]
    goal = plan["outline"][index] if index < len(plan["outline"]) else "Continue the story."
    return f"""STORY: {plan['title']}
CASE: {plan['case']}
FULL OUTLINE:
{outline}

{continuity}

NOW WRITE SECTION {index + 1} of {len(roles)} ({role}): {goal}
{ROLE_RULES[role]}
Write about {SECTION_WORDS} words of narration, split into scenes of about {SCENE_WORDS} words each (one short sentence, two at most, per scene). Continue naturally from the last line.
Per scene: "visual" is ONE sentence describing a concrete image that matches the narration of THAT scene (its place, year and objects).
"impact" is true only for the single most dramatic reveal of the section (at most one scene, usually none), otherwise false.
"label" is a clean on-screen caption of at most 4 words (a date, a place or a key number, like "London, 2001") only when the scene introduces it; otherwise an empty string. Use it in at most one scene out of eight.

Return JSON:
{{"scenes": [{{"narration": "text the narrator reads", "visual": "one-sentence image description", "impact": false, "label": ""}}],
"summary": "two sentences summarizing only the facts told in this section"}}"""


def _validate_section(data):
    scenes = []
    for scene in data.get("scenes", []):
        narration = str(scene.get("narration", "")).strip()
        visual = str(scene.get("visual", "")).strip()
        if narration and visual:
            label = str(scene.get("label", "") or "").strip()[:40]
            scenes.append(
                {
                    "narration": narration,
                    "visual": visual,
                    "impact": scene.get("impact") is True,
                    "label": label,
                }
            )
    scenes = script_cleaner.clean_scenes(scenes)
    # no máximo uma cena de impacto por seção
    seen = False
    for scene in scenes:
        if scene.get("impact"):
            if seen:
                scene["impact"] = False
            seen = True
    words = sum(len(s["narration"].split()) for s in scenes)
    if len(scenes) < 3 or words < SECTION_WORDS * 0.5:
        raise ValueError(f"Seção curta demais ({words} palavras)")
    summary = str(data.get("summary", "")).strip()
    if not summary:
        summary = " ".join(" ".join(s["narration"] for s in scenes).split()[:50])
    return {"scenes": scenes, "summary": summary, "words": words}


def _load_progress(path, theme_key):
    if not path.exists():
        return None
    try:
        data = load_json(path)
    except Exception:
        warn("Arquivo de progresso corrompido; começando do zero")
        return None
    if data.get("theme") != theme_key:
        warn(f"Progresso salvo é do tema '{data.get('theme')}'; começando do zero")
        return None
    return data


def _assemble(progress):
    plan = progress["plan"]
    scenes = []
    for section_index, section in enumerate(progress["sections"]):
        for scene in section["scenes"]:
            item = {
                "id": len(scenes) + 1,
                "narration": scene["narration"],
                "visual": scene["visual"],
                "section": section_index,
            }
            if scene.get("impact"):
                item["impact"] = True
            if scene.get("label"):
                item["label"] = scene["label"]
            if scene.get("pause"):
                item["pause"] = scene["pause"]
            scenes.append(item)
    return {
        "title": plan["title"],
        "description": plan["description"],
        "tags": plan["tags"],
        "scenes": scenes,
        "theme": progress["theme"],
        "word_count": sum(len(s["narration"].split()) for s in scenes),
    }


def write_story(theme, minutes=8, scene_count=None, progress_path=None):
    path = Path(progress_path) if progress_path else PROGRESS_FILE
    roles = _roles(minutes)
    count = len(roles)
    client = Groq(api_key=config.GROQ_API_KEY)

    progress = _load_progress(path, theme["key"])
    if progress:
        log(f"Retomando roteiro: {len(progress['sections'])}/{count} seções prontas")
    else:
        progress = {"theme": theme["key"], "plan": None, "sections": []}

    if progress["plan"] is None:
        log(f"Planejando história: {theme['name']}")
        progress["plan"] = _json_call(
            client, _plan_prompt(theme, roles), PLAN_MAX_TOKENS, lambda d: _validate_plan(d, count)
        )
        save_json(progress, path)
    plan = progress["plan"]

    generated = False
    for index in range(len(progress["sections"]), count):
        if generated:
            _pause()
        log(f"Seção {index + 1}/{count} ({roles[index]})")
        section = _json_call(
            client, _section_prompt(plan, roles, index, progress["sections"]), SECTION_MAX_TOKENS, _validate_section
        )
        section["role"] = roles[index]
        progress["sections"].append(section)
        save_json(progress, path)
        generated = True

    story = _assemble(progress)
    path.unlink(missing_ok=True)
    log(f"Roteiro pronto: '{story['title']}' ({story['word_count']} palavras, {len(story['scenes'])} cenas)")
    return story
