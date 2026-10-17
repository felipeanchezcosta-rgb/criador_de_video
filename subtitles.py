"""Legenda sincronizada com a narração (sem API, sem Whisper).

Cada cena tem texto e duração reais do áudio. As palavras são distribuídas no tempo pelo peso
(tamanho da palavra + pausa da pontuação), o que acompanha bem a voz sintética. Gera .ass
(gravado no vídeo) e .srt (para subir no YouTube). Rótulos curtos ("London, 2001") viram
texto limpo no canto superior, no lugar de texto gerado pela IA.
"""
import re
from pathlib import Path

LEAD = 0.05  # a voz começa um instante depois do início da cena
MAX_WORDS = 6
LABEL_SECONDS = 3.5


def _weights(words):
    out = []
    for word in words:
        weight = len(re.sub(r"\W", "", word)) + 2
        if word.endswith((",", ";", ":")):
            weight += 3
        elif word.endswith((".", "?", "!", "…")):
            weight += 6
        out.append(weight)
    return out


def _chunks(words):
    chunks, current = [], []
    for index, word in enumerate(words):
        current.append(index)
        ends = word.endswith((".", "?", "!", "…"))
        soft = word.endswith((",", ";", ":")) and len(current) >= 3
        if len(current) >= MAX_WORDS or ends or soft:
            chunks.append(current)
            current = []
    if current:
        if chunks and len(current) < 2:
            chunks[-1].extend(current)
        else:
            chunks.append(current)
    return chunks


def build_events(plan):
    events, labels = [], []
    start = 0.0
    for scene in plan["scenes"]:
        words = str(scene.get("text", "")).split()
        speech = float(scene.get("speech", scene["duration"]))
        if words and speech > 0:
            weights = _weights(words)
            total = float(sum(weights))
            begin, marks = start + LEAD, []
            acc = 0.0
            for weight in weights:
                marks.append(begin + speech * acc / total)
                acc += weight
            end_of_speech = begin + speech
            groups = _chunks(words)
            for position, group in enumerate(groups):
                first, last = group[0], group[-1]
                t0 = marks[first]
                t1 = marks[groups[position + 1][0]] if position + 1 < len(groups) else end_of_speech
                events.append((t0, max(t1, t0 + 0.4), " ".join(words[first:last + 1])))
        label = str(scene.get("label", "")).strip()
        if label:
            labels.append((start + 0.2, start + 0.2 + min(LABEL_SECONDS, scene["duration"]), label))
        start += scene["duration"]
    return events, labels


def _ass_time(seconds):
    seconds = max(seconds, 0)
    cs = int(round(seconds * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _srt_time(seconds):
    ms = int(round(max(seconds, 0) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _escape(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def write_files(plan, project_dir):
    """Grava subtitles.ass e subtitles.srt. Devolve o caminho do .ass."""
    project_dir = Path(project_dir)
    width, height = plan["resolution"]
    events, labels = build_events(plan)
    font = int(height * 0.052)
    label_font = int(height * 0.04)
    margin_v = int(height * 0.07)
    margin_x = int(width * 0.08)

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Sub,DejaVu Sans,{font},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3.5,1,2,"
        f"{margin_x},{margin_x},{margin_v},1",
        f"Style: Label,DejaVu Sans,{label_font},&H0000E5FF,&H000000FF,&HAA000000,&H00000000,-1,0,0,0,100,100,1,0,3,10,0,7,"
        f"60,60,60,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for t0, t1, text in events:
        lines.append(f"Dialogue: 0,{_ass_time(t0)},{_ass_time(t1)},Sub,,0,0,0,,{_escape(text)}")
    for t0, t1, text in labels:
        lines.append(f"Dialogue: 1,{_ass_time(t0)},{_ass_time(t1)},Label,,0,0,0,,{_escape(text.upper())}")
    ass_path = project_dir / "subtitles.ass"
    ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    srt = []
    for number, (t0, t1, text) in enumerate(events, start=1):
        srt.append(f"{number}\n{_srt_time(t0)} --> {_srt_time(t1)}\n{text}\n")
    (project_dir / "subtitles.srt").write_text("\n".join(srt), encoding="utf-8")
    return str(ass_path)
