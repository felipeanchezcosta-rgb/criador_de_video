import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

import config
from utils import log, make_progress, media_duration, require_ffmpeg, run_ffmpeg, warn

PRESCALE = float(os.getenv("ZOOMPAN_PRESCALE", "1.5"))
SCANLINES = os.getenv("SCANLINES", "1") != "0"
CRF = os.getenv("VIDEO_CRF", "20")
PRESET = os.getenv("VIDEO_PRESET", "veryfast")


def _make_scanlines(path, width, height):
    """PNG transparente com linhas horizontais bem sutis (visual 'tela de hacker')."""
    if Path(path).exists():
        return
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for y in range(0, height, 4):
        draw.line([(0, y), (width, y)], fill=(0, 0, 0, 38))
    img.save(path)


def _motion(effect, frames, strength, shake, focus=(0.5, 0.5)):
    fx, fy = focus
    p = f"on/{frames}"
    z_in = f"1+{strength}*{p}"
    z_out = f"1+{strength}*(1-{p})"
    z_fix = f"{1 + strength}"
    cx, cy = "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
    table = {
        "zoom_in": (z_in, cx, cy),
        "zoom_out": (z_out, cx, cy),
        "pan_left": (z_fix, f"(iw-iw/zoom)*(1-{p})", cy),
        "pan_right": (z_fix, f"(iw-iw/zoom)*{p}", cy),
        "pan_up": (z_fix, cx, f"(ih-ih/zoom)*(1-{p})"),
        "pan_down": (z_fix, cx, f"(ih-ih/zoom)*{p}"),
        "push_in": (z_in, f"(iw-iw/zoom)*{fx}", f"(ih-ih/zoom)*{fy}"),
        "pull_out": (z_out, f"(iw-iw/zoom)*{fx}", f"(ih-ih/zoom)*{fy}"),
    }
    z, x, y = table[effect]
    if shake:
        amp = 10 * PRESCALE
        x = f"{x}+{amp:.1f}*sin(on*2.3)"
        y = f"{y}+{amp:.1f}*cos(on*1.9)"
    return z, x, y


def _crop_box(iw, ih, crop, width, height):
    fx, fy, fw = crop
    ratio = width / height
    cw = int(iw * fw)
    ch = int(cw / ratio)
    if ch > ih:
        ch = ih
        cw = int(ch * ratio)
    cw -= cw % 2
    ch -= ch % 2
    x = int((iw - cw) * fx)
    y = int((ih - ch) * fy)
    return cw, ch, x, y


def _shot_command(image, shot, width, height, fps, scan_path, out_path):
    with Image.open(image) as img:
        iw, ih = img.size
    cw, ch, x, y = _crop_box(iw, ih, shot["crop"], width, height)
    frames = shot["frames"]
    z, zx, zy = _motion(shot["effect"], frames, shot["strength"], shot["shake"], shot.get("focus", (0.5, 0.5)))
    pre_w, pre_h = int(width * PRESCALE), int(height * PRESCALE)

    chain = [
        f"crop={cw}:{ch}:{x}:{y}",
        f"scale={pre_w}:{pre_h}:flags=bicubic",
        f"zoompan=z='{z}':x='{zx}':y='{zy}':d={frames}:s={width}x{height}:fps={fps}",
        f"eq=gamma={shot.get('gamma', 1.0)}:contrast=1.08:saturation=1.1",
        "unsharp=5:5:0.5:5:5:0.0",
        "vignette=angle=PI/5",
    ]
    if shot.get("grain"):
        chain.append("noise=alls=9:allf=t")
    if shot["glitch"]:
        chain.append("rgbashift=rh=-8:bh=8:rv=3:enable='between(n,0,5)'")
    if shot["flash"]:
        chain.append("fade=t=in:s=0:n=4:color=white")
    if shot["fade_in"]:
        chain.append(f"fade=t=in:s=0:n={int(fps * 0.8)}")
    if shot["fade_out"]:
        n = int(fps * 1.2)
        chain.append(f"fade=t=out:s={max(frames - n, 0)}:n={n}")

    inputs = ["-i", str(image)]
    if SCANLINES:
        inputs += ["-i", str(scan_path)]
        graph = f"[0:v]{','.join(chain)}[v];[v][1:v]overlay=format=auto,format=yuv420p[out]"
    else:
        graph = f"[0:v]{','.join(chain)},format=yuv420p[out]"

    return inputs + [
        "-filter_complex", graph,
        "-map", "[out]",
        "-frames:v", str(frames),
        "-r", str(fps),
        "-c:v", "libx264",
        "-preset", PRESET,
        "-crf", CRF,
        "-pix_fmt", "yuv420p",
        "-an",
        str(out_path),
    ]


def _render_shot(job):
    index, image, shot, width, height, fps, scan_path, out_path = job
    out_path = Path(out_path)
    if out_path.exists() and out_path.stat().st_size > 0:
        return index
    tmp = out_path.with_suffix(".part.mp4")
    run_ffmpeg(_shot_command(image, shot, width, height, fps, scan_path, tmp), f"corte {index}")
    os.replace(tmp, out_path)
    return index


def _pad_scene_audio(scene, out_path):
    out_path = Path(out_path)
    if out_path.exists() and out_path.stat().st_size > 0:
        return
    dur = f"{scene['duration']:.4f}"
    run_ffmpeg(
        [
            "-i", scene["audio"],
            "-af", f"apad=whole_dur={dur},aresample=44100",
            "-ac", "2",
            "-t", dur,
            str(out_path),
        ],
        f"áudio da cena {scene['id']}",
    )


def _write_concat(list_path, files):
    lines = []
    for f in files:
        safe = str(Path(f).resolve()).replace("'", "'\\''")
        lines.append(f"file '{safe}'")
    Path(list_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _has_ass_filter():
    """O ffmpeg do sistema precisa ter o filtro 'ass' (libass) para queimar legendas."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    except Exception:
        return False
    return any(line.split()[1:2] == ["ass"] for line in out.splitlines())


def _filter_path(path):
    """Caminho dentro de um filtro do ffmpeg: escapa a barra invertida e os dois-pontos."""
    return str(Path(path).resolve()).replace("\\", "\\\\").replace(":", "\\:")


def render(plan):
    require_ffmpeg()
    width, height = plan["resolution"]
    fps = plan["fps"]
    output = Path(plan["output"])
    work = output.parent / "render"
    work.mkdir(parents=True, exist_ok=True)

    scan_path = work / "scanlines.png"
    if SCANLINES:
        _make_scanlines(scan_path, width, height)

    jobs, shot_files = [], []
    number = 0
    for scene in plan["scenes"]:
        for shot in scene["shots"]:
            number += 1
            out = work / f"shot_{number:04d}.mp4"
            shot_files.append(out)
            jobs.append((number, scene["image"], shot, width, height, fps, scan_path, out))

    log(f"Renderizando {len(jobs)} cortes ({config.RENDER_WORKERS} em paralelo)")
    with make_progress() as progress:
        task = progress.add_task("Cortes", total=len(jobs))
        with ThreadPoolExecutor(max_workers=config.RENDER_WORKERS) as pool:
            for _ in pool.map(_render_shot, jobs):
                progress.advance(task)

    log("Preparando o áudio")
    audio_files = []
    for scene in plan["scenes"]:
        part = work / f"audio_{scene['id']:03d}.wav"
        _pad_scene_audio(scene, part)
        audio_files.append(part)

    video_list, audio_list = work / "video.txt", work / "audio.txt"
    _write_concat(video_list, shot_files)
    _write_concat(audio_list, audio_files)

    total = plan["total_frames"] / fps
    fade_out_at = max(total - 3, 0)
    graph = (
        f"[2:a]volume={plan['music']['volume']},"
        f"afade=t=in:st=0:d=2,afade=t=out:st={fade_out_at:.2f}:d=3[m];"
        f"[1:a][m]amix=inputs=2:duration=first:dropout_transition=0,volume=2,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    )

    captions = plan.get("captions")
    if captions and not Path(captions).exists():
        captions = None
    if captions and not _has_ass_filter():
        warn("Seu ffmpeg não tem o filtro de legenda (libass). O vídeo sai sem legenda; use captions.srt no YouTube.")
        captions = None

    if captions:
        # Uma única passada: queima a legenda e grava o vídeo final (o vídeo é recodificado nesta etapa).
        graph += f";[0:v]ass='{_filter_path(captions)}'[v]"
        video_args = [
            "-map", "[v]",
            "-c:v", "libx264", "-preset", PRESET, "-crf", CRF, "-pix_fmt", "yuv420p",
        ]
        log(f"Montando o vídeo final com legenda: {output.name} (esta etapa demora mais)")
    else:
        video_args = ["-map", "0:v", "-c:v", "copy"]
        log(f"Montando o vídeo final: {output.name}")

    run_ffmpeg(
        [
            "-f", "concat", "-safe", "0", "-i", str(video_list),
            "-f", "concat", "-safe", "0", "-i", str(audio_list),
            "-stream_loop", "-1", "-i", plan["music"]["path"],
            "-filter_complex", graph,
            *video_args,
            "-map", "[a]",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{total:.3f}",
            "-movflags", "+faststart",
            str(output),
        ],
        "montagem final",
    )
    log(f"Duração final: {media_duration(output):.1f}s")
    return str(output)
