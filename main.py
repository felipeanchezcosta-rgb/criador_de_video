import argparse
import subprocess
import sys
from pathlib import Path

import config
import edit_plan
import image_generator
import image_prompts
import music
import narration
import roteiro_proprio
import story_writer
import themes
import video_editor
import vision_analysis
from utils import (
    error,
    format_seconds,
    load_json,
    log,
    log_memory,
    new_project_dir,
    save_json,
    success,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Gerador de vídeos dark de tecnologia")
    parser.add_argument("--theme", help="número (1-10) ou chave do tema; sem isso aparece o menu")
    parser.add_argument("--minutes", type=float, default=10, help="duração alvo em minutos (padrão 10)")
    parser.add_argument("--project", help="pasta de um projeto existente para retomar")
    parser.add_argument("--script", help="caminho de um .txt com o SEU roteiro (pula a geração do roteiro)")
    parser.add_argument("--title", help="título do vídeo (só com o seu roteiro; sem isso o Groq sugere)")
    parser.add_argument("--list-themes", action="store_true", help="mostra os temas e sai")
    parser.add_argument("--no-video", action="store_true", help="para antes da renderização")
    parser.add_argument("--no-vision", action="store_true", help="não usa o Groq para olhar as imagens")
    return parser.parse_args()


def run_narration(project):
    """Narração em processo separado: ao terminar, o modelo de voz libera toda a RAM."""
    done = project / "narration.json"
    story = load_json(project / "story.json")
    if done.exists():
        results = load_json(done)
        if len(results) == len(story["scenes"]) and all(Path(r["path"]).exists() for r in results):
            log("Narração já pronta, pulando.")
            return results
    code = subprocess.run([sys.executable, str(Path(__file__).with_name("narrate_worker.py")), str(project)]).returncode
    if code != 0 or not done.exists():
        raise RuntimeError(
            "A narração falhou (veja a mensagem acima). Se foi falta de memória, feche o navegador "
            "e rode fora do VS Code, num terminal comum."
        )
    return load_json(done)


def write_metadata(story, project):
    lines = [
        f"TITLE:\n{story['title']}",
        f"\nDESCRIPTION:\n{story.get('description', '')}",
        "\nTAGS:\n" + ", ".join(story.get("tags", [])),
    ]
    (project / "metadata.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def choose_script_mode(args):
    """Decide se o roteiro é gerado pelo Groq ('auto') ou é o do usuário ('own')."""
    if args.script:
        return "own"
    if args.theme:
        return "auto"
    if sys.stdin.isatty():
        return roteiro_proprio.ask_mode()
    return "auto"


def main():
    args = parse_args()
    if args.list_themes:
        themes.print_menu()
        return 0

    problems = config.invalid_providers()
    if problems:
        for problem in problems:
            error(problem)
        return 1
    missing = config.missing_keys()
    if missing:
        error("Variáveis faltando no .env: " + ", ".join(missing))
        return 1

    if args.project:
        project = Path(args.project)
        story = load_json(project / "story.json")
        log(f"Retomando projeto: {project.name}")
    else:
        mode = choose_script_mode(args)
        if mode == "own":
            text = roteiro_proprio.read_script_text(args.script)
            title = args.title
            if not title and sys.stdin.isatty():
                title = roteiro_proprio.ask_title()
            log("Usando o seu roteiro")
            story = roteiro_proprio.build_story(text, title)
        else:
            if args.theme:
                theme = themes.get_theme(args.theme)
            elif sys.stdin.isatty():
                theme = themes.choose_theme_interactive()
            else:
                theme = themes.get_theme()
            log(f"Tema: {theme['name']}")
            story = story_writer.write_story(theme, minutes=args.minutes)
        project = new_project_dir(config.OUTPUT_DIR, story["title"])
        save_json(story, project / "story.json")
        write_metadata(story, project)
        log(f"Projeto criado: {project}")

    prompts_path = project / "prompts.json"
    if prompts_path.exists():
        prompts = load_json(prompts_path)
    else:
        prompts = image_prompts.generate_prompts(story, prompts_path)

    log_memory("início")
    images = image_generator.generate_images(prompts, project / "images")

    narrations = run_narration(project)
    log_memory("depois da narração")

    vision = [None] * len(images)
    if not args.no_vision:
        vision = vision_analysis.analyze_images(images, project / "vision.json")

    total = sum(n["duration"] for n in narrations) + 3
    music_path = music.get_music(total, project, story)
    plan = edit_plan.build_plan(story, images, narrations, music_path, project, vision)
    log_memory("antes de renderizar")

    if args.no_video:
        success(f"Pronto sem renderizar: {project}")
        return 0

    output = video_editor.render(plan)
    success(f"Vídeo final ({format_seconds(plan['total_duration'])}): {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
