"""Usa um roteiro escrito pelo usuário em vez de gerar um com o Groq.

O usuário entrega só o texto da narração. Este módulo:
  1. divide o texto em cenas de uns 26 palavras (cada cena = uma imagem + uma faixa de voz);
  2. pede ao Groq uma descrição visual por cena (para gerar as imagens);
  3. pede ao Groq título, descrição e tags (a menos que o usuário dê o título).

Devolve um dicionário no mesmo formato que story_writer.write_story(), então o resto do
pipeline (prompts, imagens, narração, edição) funciona sem mudanças.

Atenção: a voz padrão (Kokoro am_michael) é em inglês. Escreva o roteiro em inglês.
"""
import json
import os
import re
from pathlib import Path

from groq import Groq

import config
import groq_helper
from utils import log, retry, sleep_with_progress, warn

GROQ_MODEL = os.getenv("GROQ_MODEL", "").strip()
SCENE_WORDS = int(os.getenv("SCENE_WORDS", "26"))
SCENE_MAX_WORDS = SCENE_WORDS + 14
MIN_LAST_SCENE_WORDS = 8
VISUAL_BATCH = int(os.getenv("CUSTOM_VISUAL_BATCH", "15"))
MAX_TOKENS = int(os.getenv("IMAGE_PROMPT_MAX_TOKENS", "2000"))

VISUAL_SYSTEM = (
    "You are a visual director for a dark cinematic tech-crime documentary. For each numbered "
    "narration line, write ONE sentence in English describing a single clear image that fits it: "
    "one clear subject such as a silhouette, servers, a night city, or a symbolic object. "
    "Never describe hands, faces, logos, app icons, chat bubbles, signs, posters or screens with words. "
    "For company or app names, use a generic symbolic object. "
    'Return ONLY JSON: {"visuals":[{"id":1,"visual":"..."}]}'
)

META_SYSTEM = (
    "You write YouTube metadata for a dark cinematic tech-crime channel for an American audience. "
    "Write in English. Be honest, no clickbait lies. "
    'Return ONLY JSON: {"title":"under 70 characters","description":"2 short paragraphs",'
    '"tags":["10 to 15 tags"]}'
)


# ---------------------------------------------------------------- perguntas
def ask_mode():
    """Pergunta se o roteiro será gerado ou será o do usuário. Devolve 'auto' ou 'own'."""
    print()
    print("Como você quer o roteiro?")
    print("  1. Gerar um roteiro automaticamente (escolho um tema)")
    print("  2. Usar o meu próprio roteiro")
    print()
    while True:
        answer = input("Escolha 1 ou 2: ").strip()
        if answer == "1":
            return "auto"
        if answer == "2":
            return "own"
        print("Opção inválida, tente de novo.")


def ask_title():
    answer = input("Título do vídeo (Enter para o Groq sugerir um): ").strip()
    return answer or None


def read_script_text(path=None):
    """Lê o roteiro de um arquivo .txt/.md ou do texto colado no terminal."""
    if not path:
        print()
        print("Seu roteiro deve ser só o texto da narração, de preferência em inglês")
        print("(a voz padrão é em inglês).")
        path = input("Caminho do arquivo .txt (ou Enter para colar o texto aqui): ").strip().strip("'\"")
    if path:
        file = Path(path).expanduser()
        if not file.exists():
            raise SystemExit(f"Arquivo não encontrado: {file}")
        text = file.read_text(encoding="utf-8")
    else:
        print("Cole o roteiro. Quando terminar, digite FIM sozinho numa linha e dê Enter:")
        lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip().upper() == "FIM":
                break
            lines.append(line)
        text = "\n".join(lines)
    text = _clean(text)
    if len(text.split()) < 30:
        raise SystemExit("O roteiro está muito curto (menos de 30 palavras).")
    return text


def _clean(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.M)  # títulos markdown
    text = re.sub(r"[*_`]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


# ----------------------------------------------------------------- cenas
def split_scenes(text):
    """Corta o texto em cenas de ~SCENE_WORDS palavras, sempre no fim de uma frase."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    chunks, current, words = [], [], 0

    def flush():
        nonlocal current, words
        if current:
            chunks.append(" ".join(current))
        current, words = [], 0

    for sentence in sentences:
        count = len(sentence.split())
        if current and words + count > SCENE_MAX_WORDS:
            flush()
        current.append(sentence)
        words += count
        if words >= SCENE_WORDS:
            flush()
    if current:
        if len(chunks) and words < MIN_LAST_SCENE_WORDS:
            chunks[-1] += " " + " ".join(current)
        else:
            flush()
    return [{"id": i, "narration": c} for i, c in enumerate(chunks, start=1)]


# ------------------------------------------------------------------ Groq
def _fallback_visual(narration):
    return (
        "a dark cinematic scene with a single lit symbolic object that illustrates: "
        + " ".join(narration.split()[:14])
    )


@retry(times=4, delay=15)
def _visuals_batch(client, batch):
    model = groq_helper.resolve_model(client, GROQ_MODEL, groq_helper.PREFERRED_FAST, "visuais")
    lines = "\n".join(f"{s['id']}. {s['narration']}" for s in batch)
    text = groq_helper.chat_text(client, model, VISUAL_SYSTEM, f"Write one visual per line:\n{lines}", MAX_TOKENS)
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    return {int(v["id"]): str(v["visual"]).strip() for v in data.get("visuals", []) if v.get("visual")}


@retry(times=3, delay=10)
def _metadata(client, text):
    model = groq_helper.resolve_model(client, GROQ_MODEL, groq_helper.PREFERRED_FAST, "metadados")
    brief = text[:2500]
    answer = groq_helper.chat_text(client, model, META_SYSTEM, f"SCRIPT:\n{brief}", 1200, temperature=0.6)
    return json.loads(answer[answer.find("{"): answer.rfind("}") + 1])


def build_story(text, title=None):
    """Transforma o roteiro do usuário no mesmo formato de story_writer.write_story()."""
    scenes = split_scenes(text)
    log(f"Roteiro próprio: {len(text.split())} palavras divididas em {len(scenes)} cenas")
    client = Groq(api_key=config.GROQ_API_KEY)

    batches = [scenes[i: i + VISUAL_BATCH] for i in range(0, len(scenes), VISUAL_BATCH)]
    for number, batch in enumerate(batches, start=1):
        log(f"Descrições visuais: lote {number}/{len(batches)}")
        try:
            visuals = _visuals_batch(client, batch)
        except Exception as exc:
            warn(f"Lote {number} falhou ({str(exc)[:120]}); usando descrição genérica")
            visuals = {}
        for scene in batch:
            scene["visual"] = visuals.get(scene["id"]) or _fallback_visual(scene["narration"])
        if number < len(batches):
            sleep_with_progress(config.GROQ_BATCH_PAUSE_SECONDS, "Pausa entre lotes do Groq")

    meta = {}
    try:
        sleep_with_progress(config.GROQ_BATCH_PAUSE_SECONDS, "Pausa antes dos metadados")
        meta = _metadata(client, text)
    except Exception as exc:
        warn(f"Não consegui gerar título/descrição/tags ({str(exc)[:120]}). Edite o metadata.txt depois.")

    final_title = (title or str(meta.get("title", "")).strip() or "Untitled Tech Story")[:100]
    return {
        "title": final_title,
        "description": str(meta.get("description", "")).strip(),
        "tags": [str(t).strip() for t in meta.get("tags", []) if str(t).strip()],
        "scenes": scenes,
        "theme": "custom-script",
        "word_count": sum(len(s["narration"].split()) for s in scenes),
    }
