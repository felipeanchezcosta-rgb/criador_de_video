"""Teste rápido da OVHcloud (Stable Diffusion XL), sem chave, em modo anônimo.

Gera poucas imagens com os prompts reais do seu projeto para você avaliar a qualidade
antes de usar no vídeo. Não gasta Groq, Cloudflare nem Pollinations.

Uso:
    python teste_ovh.py                       # usa um prompt de exemplo (3 imagens)
    python teste_ovh.py <pasta_do_projeto>    # usa os 3 primeiros prompts do prompts.json
    python teste_ovh.py <pasta_do_projeto> 5  # usa os 5 primeiros
As imagens ficam na pasta teste_ovh/.
"""
import sys
import time
from pathlib import Path

import image_generator
from utils import load_json, log, warn

EXAMPLE = (
    "a lone silhouette in front of a glowing CRT monitor in a dark London bedroom, dial-up modem beside it, "
    "volumetric haze, dark cinematic digital illustration, moody lighting with a clearly lit focal point, "
    "teal and crimson neon accents, high contrast, no text, no letters, no logos, no watermark"
)


def main():
    project = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    if project and (project / "prompts.json").exists():
        prompts = load_json(project / "prompts.json")[:count]
        log(f"Usando {len(prompts)} prompts de {project.name}")
    else:
        prompts = [EXAMPLE] * count
        log("Usando o prompt de exemplo")

    out = Path("teste_ovh")
    out.mkdir(exist_ok=True)
    started = time.time()
    for i, prompt in enumerate(prompts, start=1):
        log(f"Imagem {i}/{len(prompts)} (a OVHcloud anônima aceita 2 por minuto, então há uma espera)")
        for attempt in range(1, 4):
            try:
                data = image_generator._ovh(prompt)
                break
            except Exception as exc:
                warn(f"Tentativa {attempt}/3 falhou: {exc}")
                data = None
        if not data:
            warn("Sem imagem. Se for erro 4xx, me mande a mensagem completa.")
            continue
        path = out / f"ovh_{i:02d}{image_generator._extension(data)}"
        path.write_bytes(data)
        log(f"Salva: {path} ({len(data) // 1024} KB)")
    log(f"Pronto em {time.time() - started:.0f}s. Abra a pasta teste_ovh/ e veja se a qualidade serve.")


if __name__ == "__main__":
    main()
