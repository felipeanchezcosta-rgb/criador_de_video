"""Gera a narração em um processo separado.

Quando este processo termina, o sistema devolve toda a memória usada pelo modelo de voz
(até 1,5 GB no Kokoro com torch). Assim sobra RAM para renderizar o vídeo.

Uso: python narrate_worker.py <pasta_do_projeto>
"""
import sys
from pathlib import Path

import narration
from utils import load_json, log_memory, save_json


def main():
    project = Path(sys.argv[1])
    story = load_json(project / "story.json")
    log_memory("narração: antes de carregar a voz")
    results = narration.generate_narration(story["scenes"], project / "audio")
    save_json(results, project / "narration.json")
    log_memory("narração: concluída")


if __name__ == "__main__":
    main()
