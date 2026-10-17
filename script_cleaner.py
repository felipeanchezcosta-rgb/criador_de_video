"""Limpa o roteiro antes da narração.

Remove o que NÃO deve ser lido em voz alta: instruções de produção, títulos de seção com
timestamp, extras (thumbnail, descrição, tags, capítulos), marcações de "(pause)" e tudo
que vem depois do "see you in the next one". As pausas viram silêncio real (campo "pause").
"""
import re

FLAGS = re.I | re.M

END_RE = re.compile(r"see you in the next one", re.I)
LONG_PAUSE_RE = re.compile(r"[\(\[]\s*long\s+pause\s*[\)\]]", re.I)
PAUSE_RE = re.compile(r"[\(\[]\s*(?:beat|pause)\s*[\)\]]", re.I)
STAGE_RE = re.compile(
    r"[\(\[][^\)\]]*\b(music|sound|sfx|silence|fade|cut to|b-roll|on-screen|screen shows|voice)\b[^\)\]]*[\)\]]",
    re.I,
)
TIMESTAMP_RE = re.compile(r"\(?\s*\d{1,2}:\d{2}\s*[–—-]\s*\d{1,2}:\d{2}\s*\)?")
TITLE_WITH_TS_RE = re.compile(
    r"\b(?:part|section|chapter|act)\s*\d+\s*[:.\-–—][^()\n]*\(\s*\d{1,2}:\d{2}\s*[–—-]\s*\d{1,2}:\d{2}\s*\)",
    re.I,
)
TITLE_ONLY_RE = re.compile(r"^\s*(?:part|section|chapter|act)\s*\d+\s*[:.\-–—]\s*[^.?!]{0,90}$", re.I)
PRODUCTION_RE = re.compile(
    r"\b(thumbnail|video title|youtube title|hashtags?|seo)\b"
    r"|^\s*(?:title|description|tags|chapters?|voice|pace|tone|style|music|visuals?|format|length|"
    r"duration|target|keywords?|notes?|narrator|cta)\s*[:\-–]"
    r"|\b(?:read|narrate) (?:this|the following)\b"
    r"|\bin a (?:deep|calm|low|slow)(?:,| and)? (?:calm |steady |cinematic )?voice\b",
    FLAGS,
)

LONG_SECONDS = 2.0
SHORT_SECONDS = 1.0


def is_production(text):
    text = str(text)
    return bool(PRODUCTION_RE.search(text) or TITLE_ONLY_RE.match(text.strip()))


def clean_text(text):
    """Devolve (texto limpo, pausa antes em s, pausa depois em s)."""
    text = str(text)
    before = after = 0.0
    start_marker = re.match(r"^\s*[\(\[]\s*(long\s+)?(?:pause|beat)\s*[\)\]]", text, re.I)
    if start_marker:
        before = LONG_SECONDS if start_marker.group(1) else SHORT_SECONDS
    after += LONG_SECONDS * len(LONG_PAUSE_RE.findall(text))
    stripped = LONG_PAUSE_RE.sub(" ", text)
    after += SHORT_SECONDS * len(PAUSE_RE.findall(stripped))
    if start_marker:
        after = max(0.0, after - (LONG_SECONDS if start_marker.group(1) else SHORT_SECONDS))
    text = PAUSE_RE.sub(" ", stripped)
    text = TITLE_WITH_TS_RE.sub(" ", text)
    text = TIMESTAMP_RE.sub(" ", text)
    text = STAGE_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text, min(before, 3.0), min(after, 3.0)


def clean_scenes(scenes):
    """Devolve uma nova lista de cenas limpa. Cada cena pode ganhar o campo "pause" (segundos)."""
    out = []
    for scene in scenes:
        raw = str(scene.get("narration", ""))
        if is_production(raw):
            continue
        text, before, after = clean_text(raw)
        if len(text.split()) < 2:
            if out and (before or after):
                out[-1]["pause"] = round(min(out[-1].get("pause", 0) + before + after, 3.0), 2)
            continue
        item = dict(scene)
        item["narration"] = text
        if before and out:
            out[-1]["pause"] = round(min(out[-1].get("pause", 0) + before, 3.0), 2)
        if after:
            item["pause"] = round(after, 2)
        out.append(item)
        if END_RE.search(text):
            break
    return out
