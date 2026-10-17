import functools
import json
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

console = Console()


def log(msg, style="cyan"):
    console.print(msg, style=style, markup=False, highlight=False)


def warn(msg):
    log(msg, "yellow")


def error(msg):
    log(msg, "bold red")


def success(msg):
    log(msg, "bold green")


def make_progress():
    return Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    )


def slugify(text, max_len=50):
    text = re.sub(r"[^a-zA-Z0-9]+", "-", str(text).lower()).strip("-")
    return text[:max_len].strip("-") or "video"


def retry(times=3, delay=3.0, exceptions=(Exception,)):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last = None
            for attempt in range(1, times + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last = exc
                    if attempt == times:
                        break
                    wait = delay * attempt
                    warn(f"{fn.__name__} falhou ({exc}). Tentativa {attempt}/{times}, aguardando {wait:.0f}s")
                    time.sleep(wait)
            raise last

        return wrapper

    return decorator


def save_json(data, path):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("JSON não encontrado na resposta")
    return json.loads(text[start : end + 1])


def new_project_dir(base, title):
    name = f"{datetime.now():%Y%m%d_%H%M}_{slugify(title)}"
    project = Path(base) / name
    (project / "images").mkdir(parents=True, exist_ok=True)
    (project / "audio").mkdir(parents=True, exist_ok=True)
    return project


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def sleep_with_progress(seconds, label="Aguardando"):
    seconds = int(seconds)
    if seconds <= 0:
        return
    with make_progress() as progress:
        task = progress.add_task(label, total=seconds)
        for _ in range(seconds):
            time.sleep(1)
            progress.advance(task)


def format_seconds(total):
    total = int(round(total))
    return f"{total // 60}m{total % 60:02d}s"


def require_ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg não encontrado. Instale com: sudo apt install ffmpeg")


def media_duration(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(f"Não consegui medir a duração de {path}: {result.stderr.strip()}")


def run_ffmpeg(args, label="ffmpeg"):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + [str(a) for a in args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{label} falhou:\n{result.stderr[-1500:]}")


def _meminfo():
    info = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            info[key] = int(value.strip().split()[0]) // 1024  # MB
    except Exception:
        pass
    return info


def free_memory_mb():
    """RAM disponível agora, em MB (None se o sistema não informar)."""
    return _meminfo().get("MemAvailable")


def log_memory(label):
    """Registra quanto de RAM e de swap há livre. Ajuda a descobrir qual etapa estoura a memória."""
    info = _meminfo()
    if not info:
        return
    avail = info.get("MemAvailable", 0)
    total = info.get("MemTotal", 0)
    swap_used = info.get("SwapTotal", 0) - info.get("SwapFree", 0)
    log(f"[memória] {label}: {avail} MB livres de {total} MB | swap em uso: {swap_used} MB", "dim")
    if avail and avail < 700:
        warn("Pouca RAM livre. Feche o navegador e outros programas, ou rode fora do VS Code.")
