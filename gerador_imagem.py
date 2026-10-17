"""
gerador_imagem.py — SpiritCode
---------------------------------
Parte 3 da automação do blog "SpiritCode" (spiritcode.blog).

Responsabilidade deste arquivo (só isso, nada além):
  1. Ler o frontmatter e o corpo de uma matéria .md (gerada pelo escritor_materias.py) —
     se houver mais de uma matéria em materias/, pergunta interativamente qual delas usar.
  2. Ler o conteúdo real do artigo e usar a Groq pra criar uma cena visual
     específica daquele tema técnico (ex: um terminal com erro de conexão,
     um dashboard de monitoramento, um diagrama de arquitetura) — em vez de
     um cenário genérico de "código na tela".
  3. Gerar a imagem via Together.ai (FLUX.1-schnell-Free) ou Pollinations.ai
     (padrão), com linguagem de fotografia editorial profissional, focada em
     ficar bonita e com cara de publicação séria (E-E-A-T visual).
  4. Gerar uma legenda curta (3-6 palavras) via Groq pra escrever por cima da
     imagem.
  5. Gerar um alt text otimizado pra SEO (via Groq) com a palavra-chave foco,
     descrevendo com precisão o que aparece na foto — isso sim pesa no SEO
     de imagem, ao contrário do prompt de geração em si.
  6. Redimensionar/cortar pra 16:9, escrever a legenda por cima, salvar em
     imagens/, e salvar o alt text num .txt do lado.

Sobre os provedores de imagem:
  - Pollinations.ai (padrão): gratuita, sem cadastro obrigatório, modelo
    gptimage-large costuma sair melhor que o flux simples.
  - Together.ai (alternativa, --provedor together): FLUX.1-schnell-Free,
    tem cota de créditos grátis no cadastro — se acabar, o script cai
    automaticamente pra Pollinations sozinho.

Como rodar:
  python gerador_imagem.py
  python gerador_imagem.py --arquivo materias/xxx.md
  python gerador_imagem.py --provedor together
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import sys
import textwrap
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import requests
import yaml
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "")
POLLINATIONS_URL = "https://image.pollinations.ai/prompt"

TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "")
TOGETHER_URL = "https://api.together.xyz/v1/images/generations"
TOGETHER_MODELO_PADRAO = "black-forest-labs/FLUX.1-schnell-Free"

PASTA_MATERIAS = Path("materias")
PASTA_IMAGENS = Path("imagens")

LARGURA_PADRAO = 1200
ALTURA_PADRAO = 675  # 16:9

FONTE_TITULO_ENV = os.getenv("FONTE_TITULO", "")
FONTES_CANDIDATAS = [
    FONTE_TITULO_ENV,
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\consolab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


# --------------------------------------------------------------------------- #
# Modelo de dados
# --------------------------------------------------------------------------- #

@dataclass
class DadosMateria:
    titulo_seo: str
    meta_descricao: str
    palavra_chave_foco: str
    slug: str
    tags: list[str]
    corpo_markdown: str


# --------------------------------------------------------------------------- #
# Leitura das matérias
# --------------------------------------------------------------------------- #

def encontrar_arquivos_materias() -> list[Path]:
    arquivos = sorted(glob.glob(str(PASTA_MATERIAS / "*.md")))
    if not arquivos:
        print(f"ERRO: nenhum .md encontrado em {PASTA_MATERIAS}/. Rode o escritor_materias.py primeiro.")
        sys.exit(1)
    return [Path(a) for a in arquivos]


def carregar_frontmatter(caminho: Path) -> DadosMateria:
    texto = caminho.read_text(encoding="utf-8")

    if not texto.startswith("---"):
        print(f"ERRO: {caminho} não tem frontmatter (bloco --- no topo).")
        sys.exit(1)

    partes = texto.split("---", 2)
    if len(partes) < 3:
        print(f"ERRO: frontmatter malformado em {caminho}.")
        sys.exit(1)

    frontmatter = yaml.safe_load(partes[1]) or {}
    corpo_markdown = partes[2].strip()

    return DadosMateria(
        titulo_seo=frontmatter.get("titulo_seo", caminho.stem),
        meta_descricao=frontmatter.get("meta_descricao", ""),
        palavra_chave_foco=frontmatter.get("palavra_chave_foco", ""),
        slug=frontmatter.get("slug", caminho.stem),
        tags=frontmatter.get("tags", []) or [],
        corpo_markdown=corpo_markdown,
    )


def escolher_arquivo_materia() -> Path:
    """Se houver mais de uma matéria na pasta, pergunta interativamente qual
    delas gerar a imagem, em vez de escolher a mais recente sozinho."""
    caminhos = encontrar_arquivos_materias()

    if len(caminhos) == 1:
        return caminhos[0]

    print("\nMatérias disponíveis:")
    for i, caminho in enumerate(caminhos, start=1):
        materia = carregar_frontmatter(caminho)
        print(f"  [{i}] {materia.titulo_seo}  ({caminho.name})")

    while True:
        escolha = input(f"\nDigite o número da matéria (1 a {len(caminhos)}): ").strip()
        if escolha.isdigit() and 1 <= int(escolha) <= len(caminhos):
            return caminhos[int(escolha) - 1]
        print(f"Número inválido. Digite um valor entre 1 e {len(caminhos)}.")


# --------------------------------------------------------------------------- #
# Cena visual específica via Groq (lendo a matéria de verdade)
# --------------------------------------------------------------------------- #

CENA_SYSTEM_PROMPT = """You help create image-generation prompts for a technical blog \
about software engineering. Given an article's title, meta description and body, \
describe, in ONE short sentence (max 30 words), a specific visual scene that represents \
the exact technical situation discussed in the article — not a generic "laptop with code" \
scene.

Rules:
- Only tech-related objects allowed: terminal windows, code editors, monitors, server
  racks, network cables, dashboards, error logs, architecture diagrams, sticky notes,
  mechanical keyboards, coffee cups on a desk.
- NEVER include readable/legible text, code, or logs in the description (rendered text
  in AI images comes out garbled) — describe shapes, colors, and glow instead (e.g.
  "a terminal window glowing with red error highlights" instead of quoting the error).
- No human figures, faces, or hands typing.
- The scene must clearly relate to the article's specific topic (e.g. an article about
  Docker permission errors should suggest a broken/blocked container icon or a padlock
  over a docker whale icon, not just "a laptop").
- Write in English, as a single descriptive phrase ready to paste into an image prompt.
- Respond with ONLY the sentence, nothing else."""


def gerar_cena_visual(materia: DadosMateria) -> str:
    if not GROQ_API_KEY or "cole_" in GROQ_API_KEY:
        print("AVISO: GROQ_API_KEY não configurada — usando cena genérica na imagem.")
        return ""

    print("Lendo a matéria e gerando a cena visual específica com a Groq ...")
    trecho_corpo = materia.corpo_markdown[:3000]

    conteudo_usuario = (
        f"Title: {materia.titulo_seo}\n"
        f"Meta description: {materia.meta_descricao}\n"
        f"Article body (excerpt):\n{trecho_corpo}"
    )

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": CENA_SYSTEM_PROMPT},
            {"role": "user", "content": conteudo_usuario},
        ],
        "temperature": 0.7,
        # reasoning_effort "low" evita que o modelo gaste o pouco de max_tokens
        # só "pensando" e volte com resposta vazia.
        "max_tokens": 400,
        "reasoning_effort": "low",
    }

    try:
        resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip().strip('"')
    except (requests.exceptions.RequestException, KeyError, IndexError) as e:
        print(f"AVISO: falha ao gerar cena visual com a Groq ({e}) — usando cena genérica.")
        return ""


# --------------------------------------------------------------------------- #
# Legenda curta via Groq
# --------------------------------------------------------------------------- #

LEGENDA_SYSTEM_PROMPT = """You write very short captions to overlay on cover photos for \
a software engineering blog. Given an article title, write a caption of 3 to 6 words, \
punchy and engineer-friendly, that captures the spirit of the topic — never a literal \
repeat of the whole title. Respond with ONLY the caption in English, no quotes, no \
trailing punctuation, no explanation."""


def gerar_legenda_curta(materia: DadosMateria) -> str:
    if not GROQ_API_KEY or "cole_" in GROQ_API_KEY:
        print("AVISO: GROQ_API_KEY não configurada — usando o título completo na imagem.")
        return materia.titulo_seo

    print("Gerando legenda curta com a Groq ...")
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": LEGENDA_SYSTEM_PROMPT},
            {"role": "user", "content": f"Article title: {materia.titulo_seo}"},
        ],
        "temperature": 0.8,
        "max_tokens": 200,
        "reasoning_effort": "low",
    }

    try:
        resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        legenda = resp.json()["choices"][0]["message"]["content"].strip().strip('"')
        return legenda or materia.titulo_seo
    except (requests.exceptions.RequestException, KeyError, IndexError) as e:
        print(f"AVISO: falha ao gerar legenda com a Groq ({e}) — usando o título completo.")
        return materia.titulo_seo


# --------------------------------------------------------------------------- #
# Alt text otimizado pra SEO
# --------------------------------------------------------------------------- #

ALT_TEXT_SYSTEM_PROMPT = """You write SEO alt text for featured images on a software \
engineering blog. Given the article's title, focus keyword and meta description, write \
ONE alt text in English, up to 125 characters, that: naturally includes the focus \
keyword, accurately and honestly describes what a viewer would see in the photo (never \
claims to show legible code or text that isn't actually legible), and reads naturally, \
not like a keyword-stuffed list. Respond with ONLY the alt text, nothing else."""


def gerar_alt_text_seo(materia: DadosMateria, cena_especifica: str) -> str:
    if not GROQ_API_KEY or "cole_" in GROQ_API_KEY:
        return materia.titulo_seo

    print("Gerando alt text otimizado pra SEO com a Groq ...")
    conteudo_usuario = (
        f"Title: {materia.titulo_seo}\n"
        f"Focus keyword: {materia.palavra_chave_foco}\n"
        f"Meta description: {materia.meta_descricao}\n"
        f"What the photo actually shows: {cena_especifica or 'a developer workstation setup'}"
    )

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": ALT_TEXT_SYSTEM_PROMPT},
            {"role": "user", "content": conteudo_usuario},
        ],
        "temperature": 0.6,
        "max_tokens": 300,
        "reasoning_effort": "low",
    }

    try:
        resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        alt_text = resp.json()["choices"][0]["message"]["content"].strip().strip('"')
        return alt_text or materia.titulo_seo
    except (requests.exceptions.RequestException, KeyError, IndexError) as e:
        print(f"AVISO: falha ao gerar alt text com a Groq ({e}) — usando o título como alt text.")
        return materia.titulo_seo


# --------------------------------------------------------------------------- #
# Montagem do prompt de imagem
# --------------------------------------------------------------------------- #

def montar_prompt_imagem(materia: DadosMateria, cena_especifica: str = "") -> str:
    tema = materia.palavra_chave_foco or materia.titulo_seo

    cena = cena_especifica or (
        "a modern developer workstation with a glowing monitor and a mechanical keyboard"
    )

    return (
        f"Professional editorial photograph, magazine cover quality. "
        f"Main subject, filling most of the frame: {cena}. "
        f"Theme: '{tema}'. "
        "Modern tech/software engineering atmosphere, "
        "no human figures, no faces, no hands, no legible text or code, "
        "cool blue and violet accent lighting mixed with warm desk lamp glow, "
        "shot on a full-frame camera with a prime lens, natural authentic "
        "lighting, balanced composition following the rule of thirds, "
        "polished color grading, no artificial or uncanny look, no distortion, "
        "shallow depth of field, rich realistic textures, highly detailed, "
        "sharp focus, trustworthy and premium editorial quality suitable for "
        "a respected tech publication, 16:9 composition"
    )


# --------------------------------------------------------------------------- #
# Geração da imagem
# --------------------------------------------------------------------------- #

def gerar_imagem_pollinations(prompt: str, largura: int, altura: int, modelo: str, seed: int | None) -> Image.Image:
    print(f"Gerando imagem via Pollinations (modelo={modelo}, {largura}x{altura}) ...")

    url = f"{POLLINATIONS_URL}/{quote(prompt)}"
    params = {
        "model": modelo,
        "width": largura,
        "height": altura,
        "nologo": "true",
        "safe": "true",
    }
    if seed is not None:
        params["seed"] = seed
    if POLLINATIONS_API_KEY:
        params["token"] = POLLINATIONS_API_KEY

    try:
        resp = requests.get(url, params=params, timeout=120)
    except requests.exceptions.RequestException as e:
        print(f"ERRO: falha de conexão com a Pollinations.ai: {e}")
        sys.exit(1)

    if resp.status_code != 200:
        print(f"ERRO ao gerar imagem ({resp.status_code}): {resp.text[:300]}")
        sys.exit(1)

    try:
        return Image.open(BytesIO(resp.content)).convert("RGB")
    except Exception:
        print("ERRO: a resposta não veio como uma imagem válida.")
        sys.exit(1)


class CreditoTogetherEsgotadoError(Exception):
    """Levantado quando a Together.ai recusa por falta de créditos (HTTP 402)."""


def gerar_imagem_together(prompt: str, largura: int, altura: int, modelo: str, seed: int | None) -> Image.Image:
    if not TOGETHER_API_KEY or "cole_" in TOGETHER_API_KEY:
        print("ERRO: preencha a TOGETHER_API_KEY no .env pra usar o provedor 'together'.\n"
              "Crie uma conta grátis em https://api.together.ai e gere uma chave em "
              "https://api.together.ai/settings/api-keys")
        sys.exit(1)

    print(f"Gerando imagem via Together.ai (modelo={modelo}) ...")

    proporcao = math.gcd(largura, altura)
    aspect_ratio = f"{largura // proporcao}:{altura // proporcao}"

    headers = {
        "Authorization": f"Bearer {TOGETHER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": modelo,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "steps": 4,
        "n": 1,
    }
    if seed is not None:
        payload["seed"] = seed

    try:
        resp = requests.post(TOGETHER_URL, headers=headers, json=payload, timeout=120)
    except requests.exceptions.RequestException as e:
        print(f"ERRO: falha de conexão com a Together.ai: {e}")
        sys.exit(1)

    if resp.status_code != 200:
        if resp.status_code == 402:
            raise CreditoTogetherEsgotadoError(resp.text[:300])
        print(f"ERRO ao gerar imagem na Together.ai ({resp.status_code}): {resp.text[:300]}")
        sys.exit(1)

    dados = resp.json()
    try:
        url_imagem = dados["data"][0]["url"]
    except (KeyError, IndexError):
        print(f"ERRO: resposta inesperada da Together.ai: {dados}")
        sys.exit(1)

    resp_imagem = requests.get(url_imagem, timeout=60)
    resp_imagem.raise_for_status()
    return Image.open(BytesIO(resp_imagem.content)).convert("RGB")


def ajustar_para_proporcao(imagem: Image.Image, largura: int, altura: int) -> Image.Image:
    proporcao_alvo = largura / altura
    w, h = imagem.size
    proporcao_atual = w / h

    if proporcao_atual > proporcao_alvo:
        nova_largura = int(h * proporcao_alvo)
        offset = (w - nova_largura) // 2
        imagem = imagem.crop((offset, 0, offset + nova_largura, h))
    else:
        nova_altura = int(w / proporcao_alvo)
        offset = (h - nova_altura) // 2
        imagem = imagem.crop((0, offset, w, offset + nova_altura))

    return imagem.resize((largura, altura), Image.LANCZOS)


# --------------------------------------------------------------------------- #
# Texto da legenda por cima da imagem
# --------------------------------------------------------------------------- #

def carregar_fonte(tamanho: int) -> ImageFont.FreeTypeFont:
    for caminho in FONTES_CANDIDATAS:
        if caminho and Path(caminho).exists():
            try:
                return ImageFont.truetype(caminho, tamanho)
            except OSError:
                continue
    print("AVISO: nenhuma fonte .ttf encontrada, usando fonte padrão do Pillow "
          "(fica pequena/feia). Defina FONTE_TITULO no .env com o caminho de uma "
          "fonte .ttf pra melhorar isso.")
    return ImageFont.load_default()


def quebrar_linhas(draw: ImageDraw.ImageDraw, texto: str, fonte: ImageFont.FreeTypeFont,
                    largura_maxima: int) -> list[str]:
    palavras = texto.split()
    linhas: list[str] = []
    linha_atual = ""

    for palavra in palavras:
        candidata = f"{linha_atual} {palavra}".strip()
        if draw.textlength(candidata, font=fonte) <= largura_maxima:
            linha_atual = candidata
        else:
            if linha_atual:
                linhas.append(linha_atual)
            linha_atual = palavra

    if linha_atual:
        linhas.append(linha_atual)
    return linhas


def adicionar_texto_legenda(imagem: Image.Image, texto: str, posicao: str = "inferior") -> Image.Image:
    imagem = imagem.convert("RGBA")
    largura, altura = imagem.size

    margem_lateral = int(largura * 0.06)
    largura_util = largura - (2 * margem_lateral)

    tamanho_fonte = max(28, int(largura * 0.045))
    fonte = carregar_fonte(tamanho_fonte)

    camada_texto = Image.new("RGBA", imagem.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(camada_texto)

    linhas = quebrar_linhas(draw, texto.upper(), fonte, largura_util)
    altura_linha = int(tamanho_fonte * 1.25)
    altura_bloco_texto = altura_linha * len(linhas)

    padding = int(altura * 0.04)
    altura_faixa = altura_bloco_texto + (padding * 2)

    topo_faixa = altura - altura_faixa if posicao == "inferior" else 0

    draw.rectangle(
        [(0, topo_faixa), (largura, topo_faixa + altura_faixa)],
        fill=(10, 10, 10, 165),
    )

    y = topo_faixa + padding
    for linha in linhas:
        largura_linha = draw.textlength(linha, font=fonte)
        x = (largura - largura_linha) / 2
        for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
            draw.text((x + dx, y + dy), linha, font=fonte, fill=(0, 0, 0, 255))
        # Ciano/azul elétrico — combina mais com identidade visual de tech
        # do que o dourado usado no Império dos Sete.
        draw.text((x, y), linha, font=fonte, fill=(80, 220, 255, 255))
        y += altura_linha

    return Image.alpha_composite(imagem, camada_texto).convert("RGB")


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #

def slugificar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return texto.strip().lower().replace(" ", "-")


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(
        description="Gera a imagem de destaque de uma matéria do SpiritCode."
    )
    parser.add_argument("--arquivo", type=str, default=None,
                         help="Caminho do .md da matéria. Se omitido e houver mais de uma "
                              "em materias/, pergunta interativamente qual usar.")
    parser.add_argument("--provedor", type=str, default="pollinations", choices=["together", "pollinations"],
                         help="Provedor de geração de imagem (padrão: pollinations).")
    parser.add_argument("--modelo", type=str, default=None,
                         help="Modelo a usar. Together: black-forest-labs/FLUX.1-schnell-Free (padrão). "
                              "Pollinations: gptimage-large (padrão), flux, turbo.")
    parser.add_argument("--largura", type=int, default=LARGURA_PADRAO)
    parser.add_argument("--altura", type=int, default=ALTURA_PADRAO)
    parser.add_argument("--seed", type=int, default=None, help="Seed fixa, pra repetir o mesmo resultado.")
    parser.add_argument("--posicao-texto", type=str, default="inferior",
                         choices=["inferior", "superior"], help="Onde colocar a faixa com a legenda.")
    parser.add_argument("--sem-texto", action="store_true",
                         help="Gera a imagem sem escrever legenda por cima.")
    args = parser.parse_args()

    caminho_materia = Path(args.arquivo) if args.arquivo else escolher_arquivo_materia()
    if not caminho_materia.exists():
        print(f"ERRO: arquivo não encontrado: {caminho_materia}")
        sys.exit(1)

    materia = carregar_frontmatter(caminho_materia)
    print(f"Matéria: {materia.titulo_seo}")

    cena_especifica = gerar_cena_visual(materia)
    prompt = montar_prompt_imagem(materia, cena_especifica)

    if args.provedor == "together":
        modelo = args.modelo or TOGETHER_MODELO_PADRAO
        try:
            imagem = gerar_imagem_together(prompt, args.largura, args.altura, modelo, args.seed)
        except CreditoTogetherEsgotadoError:
            print("AVISO: a Together.ai recusou por falta de créditos. "
                  "Caindo automaticamente pra Pollinations.ai pra essa imagem...")
            imagem = gerar_imagem_pollinations(prompt, args.largura, args.altura, "gptimage-large", args.seed)
    else:
        modelo = args.modelo or "gptimage-large"
        imagem = gerar_imagem_pollinations(prompt, args.largura, args.altura, modelo, args.seed)

    imagem_final = ajustar_para_proporcao(imagem, args.largura, args.altura)

    if not args.sem_texto:
        legenda = gerar_legenda_curta(materia)
        imagem_final = adicionar_texto_legenda(imagem_final, legenda, args.posicao_texto)

    PASTA_IMAGENS.mkdir(exist_ok=True)
    caminho_saida = PASTA_IMAGENS / f"{caminho_materia.stem}.jpg"
    imagem_final.save(caminho_saida, "JPEG", quality=90)

    alt_text = gerar_alt_text_seo(materia, cena_especifica)
    caminho_alt = caminho_saida.with_suffix(".alt.txt")
    caminho_alt.write_text(alt_text, encoding="utf-8")

    print(f"\nImagem salva em: {caminho_saida}")
    print(f"Alt text (SEO) salvo em: {caminho_alt}")
    print(f"  -> \"{alt_text}\"")
    print("  (cole esse texto no campo 'Alt Text' da imagem no WordPress)")


if __name__ == "__main__":
    main()
