"""
escritor_materias.py — SpiritCode
------------------------------------
Parte 2 da automação do blog "SpiritCode" (spiritcode.blog).

Responsabilidade deste arquivo (só isso, nada além):
  1. Ler o .json de ideias gerado pelo gerador_temas.py (pega automaticamente
     o mais recente da pasta saidas/, ou você aponta um específico).
  2. Você escolhe quais números da lista quer escrever.
  3. Pra cada ideia escolhida, monta um prompt seguindo o padrão editorial fixo
     do SpiritCode e manda pra Groq escrever a matéria completa em inglês.
  4. Salva cada matéria como um .md separado na pasta materias/, com bloco de
     metadados (título, meta description, slug, palavra-chave foco, tags) no
     topo, pronta pra revisão e, depois, postagem.
  5. Marca o título como "já publicado" no temas_publicados.txt — o mesmo
     arquivo que o gerador_temas.py consulta pra não sugerir tema repetido.
     Só entra aqui, quando o artigo é escrito de verdade (nem toda ideia
     gerada vira matéria).

Este arquivo NÃO publica nada — só gera o conteúdo.

Como rodar:
  python escritor_materias.py
  python escritor_materias.py --arquivo saidas/ideias_temas_20260803_120000.json
  python escritor_materias.py --indices 1,7
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

PASTA_IDEIAS = Path("saidas")
PASTA_MATERIAS = Path("materias")

# Mesmo arquivo que o gerador_temas.py consulta pra não repetir tema. É aqui
# que a matéria entra na lista de "já publicado" — só quando o artigo é
# realmente escrito, não toda ideia que só foi gerada e descartada.
ARQUIVO_TEMAS_PUBLICADOS = Path("temas_publicados.txt")


def marcar_tema_como_publicado(titulo: str) -> None:
    if not titulo:
        return

    ja_salvos: set[str] = set()
    if ARQUIVO_TEMAS_PUBLICADOS.exists():
        ja_salvos = {
            linha.strip()
            for linha in ARQUIVO_TEMAS_PUBLICADOS.read_text(encoding="utf-8").splitlines()
            if linha.strip()
        }

    if titulo in ja_salvos:
        return

    with open(ARQUIVO_TEMAS_PUBLICADOS, "a", encoding="utf-8") as f:
        f.write(titulo + "\n")


# --------------------------------------------------------------------------- #
# Modelo de dados
# --------------------------------------------------------------------------- #

@dataclass
class Ideia:
    numero: int
    vertente: str
    titulo: str
    micro_nicho: str
    intencao_busca: str

    @classmethod
    def de_dict(cls, d: dict) -> "Ideia":
        return cls(
            numero=d.get("numero"),
            vertente=d.get("vertente", ""),
            titulo=d.get("titulo", ""),
            micro_nicho=d.get("microNicho", ""),
            intencao_busca=d.get("intencaoBusca", ""),
        )


# --------------------------------------------------------------------------- #
# Prompt — padrão editorial fixo do SpiritCode
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """You are a senior software engineer writing for "SpiritCode", an \
English-language technical blog for US-based developers. You always write in first \
person, as an engineer who has actually hit this exact problem in production or in a \
real project — never as a distant, generic tutorial voice.

Follow this editorial standard STRICTLY for every article:

1. STRUCTURE
   - H1 with the given title (or a natural variation of it).
   - A first-person intro that briefly sets up the real situation where this problem
     comes up before diving into the fix.
   - Subheadings (H2/H3) phrased as long-tail questions whenever it makes sense,
     matching how developers actually search for this on Google.
   - A conclusion with a genuine engineering takeaway, not a generic wrap-up.
   - The full article (corpo_markdown) must be AT LEAST 2000 words. Develop each
     section with real depth (concrete examples, context, edge cases) — never pad
     it with filler just to hit the count.

2. CODE EXAMPLES
   - Include real, correct, runnable code examples in properly fenced markdown code
     blocks with the right language tag (```javascript, ```python, ```bash, ```yaml,
     ```dockerfile, etc.).
   - Show the broken/problematic version when relevant, then the fixed version.
   - Never invent APIs, flags, or config options that don't exist. If unsure about an
     exact detail (a version number, a specific flag), speak more generally instead of
     making it up.

3. BLOCKQUOTES — use "Pro Tip" blockquotes at least twice through the article, in
   markdown blockquote format:
   > **Pro Tip:** (a practical, specific tip a senior engineer would actually give)

4. FAQ
   - Before the conclusion, include a "## FAQ" section with 4 to 6 long-tail questions
     (phrased the way developers actually search on Google), each answered in 2 to 4
     objective sentences, optimized to appear in a featured snippet.

5. CTA
   - End the article with a short, low-key call to action inviting readers to keep
     following SpiritCode for more posts like this.
   - Do NOT include any link, handle, or URL in the CTA — just the text. The link will
     be added manually later.
   - NEVER include or suggest a comments section.

6. SEO
   - Use the focus keyword (the title itself, phrased naturally) in the first
     paragraph, in at least one H2, and in the conclusion.
   - Correct heading hierarchy (a single H1, H2 for main sections, H3 for
     sub-sections when needed).

7. TONE
   - Direct, practical, engineer-to-engineer American English. No fluff, no filler
     intros like "In today's fast-paced world of software development".
   - Assume the reader is a working developer, not a beginner needing basics explained.

OUTPUT FORMAT — respond ONLY with valid JSON, no text before or after, in this format:

{
  "meta_titulo_seo": "SEO-optimized title, up to 60 characters",
  "meta_descricao": "SEO meta description, up to 155 characters, including the focus keyword",
  "palavra_chave_foco": "main focus keyword of the article",
  "slug": "kebab-case-slug-no-accents",
  "tags": ["tag1", "tag2", "tag3"],
  "corpo_markdown": "the full article in markdown, following all the rules above"
}
"""

USER_PROMPT_TEMPLATE = """Write the complete article for SpiritCode based on this idea:

Category: {vertente}
Title: {titulo}
Micro-niche / specific problem to solve: {micro_nicho}
Search intent: {intencao_busca}

Respond only with the requested JSON in the specified format."""


# --------------------------------------------------------------------------- #
# Funções auxiliares
# --------------------------------------------------------------------------- #

def encontrar_arquivo_ideias_mais_recente() -> Path:
    arquivos = sorted(glob.glob(str(PASTA_IDEIAS / "ideias_temas_*.json")))
    if not arquivos:
        print(f"ERRO: nenhum arquivo de ideias encontrado em {PASTA_IDEIAS}/. "
              f"Rode o gerador_temas.py primeiro.")
        sys.exit(1)
    return Path(arquivos[-1])


def carregar_ideias(caminho: Path) -> list[Ideia]:
    with open(caminho, "r", encoding="utf-8") as f:
        dados = json.load(f)
    return [Ideia.de_dict(d) for d in dados.get("ideias", [])]


def escolher_indices(argumento: Optional[str], ideias: list[Ideia]) -> list[int]:
    if argumento:
        indices = [int(x.strip()) for x in argumento.split(",") if x.strip()]
    else:
        print("\nIdeias disponíveis:")
        for ideia in ideias:
            print(f"  [{ideia.numero}] ({ideia.vertente}) {ideia.titulo}")
        bruto = input("\nDigite os números das matérias que quer escrever "
                       "(separados por vírgula): ")
        indices = [int(x.strip()) for x in bruto.split(",") if x.strip()]

    numeros_validos = {ideia.numero for ideia in ideias}
    invalidos = [i for i in indices if i not in numeros_validos]
    if invalidos:
        print(f"ERRO: número(s) inválido(s): {invalidos}")
        sys.exit(1)
    return indices


def slugificar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9\s-]", "", texto).strip().lower()
    return re.sub(r"[\s_-]+", "-", texto)


def chamar_groq(system_prompt: str, user_prompt: str) -> dict:
    if not GROQ_API_KEY or "cole_" in GROQ_API_KEY:
        print("ERRO: preencha a GROQ_API_KEY no arquivo .env antes de rodar.")
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.85,
        # O plano gratuito da Groq limita esse modelo a 8000 tokens por minuto,
        # contando prompt + reasoning + resposta. reasoning_effort "low" faz o
        # modelo "pensar menos" e sobrar mais tokens pra escrever o artigo.
        "max_tokens": 6800,
        "reasoning_effort": "low",
        "response_format": {"type": "json_object"},
    }

    resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=120)

    if resp.status_code != 200:
        print(f"ERRO na chamada da API Groq ({resp.status_code}): {resp.text}")
        sys.exit(1)

    data = resp.json()
    conteudo = data["choices"][0]["message"]["content"]

    try:
        return json.loads(conteudo)
    except json.JSONDecodeError:
        print("ERRO: a resposta da Groq não veio em JSON válido. Resposta bruta:")
        print(conteudo)
        sys.exit(1)


def montar_markdown_final(artigo: dict) -> str:
    tags = artigo.get("tags", [])
    frontmatter = (
        "---\n"
        f"titulo_seo: \"{artigo.get('meta_titulo_seo', '')}\"\n"
        f"meta_descricao: \"{artigo.get('meta_descricao', '')}\"\n"
        f"palavra_chave_foco: \"{artigo.get('palavra_chave_foco', '')}\"\n"
        f"slug: \"{artigo.get('slug', '')}\"\n"
        f"tags: {json.dumps(tags, ensure_ascii=False)}\n"
        "---\n\n"
    )
    return frontmatter + artigo.get("corpo_markdown", "")


def gerar_materia(ideia: Ideia) -> Path:
    print(f"\nEscrevendo matéria [{ideia.numero}] {ideia.titulo} ...")

    user_prompt = USER_PROMPT_TEMPLATE.format(
        vertente=ideia.vertente,
        titulo=ideia.titulo,
        micro_nicho=ideia.micro_nicho,
        intencao_busca=ideia.intencao_busca,
    )
    artigo = chamar_groq(SYSTEM_PROMPT, user_prompt)

    slug = artigo.get("slug") or slugificar(ideia.titulo)
    slug = slugificar(slug)

    PASTA_MATERIAS.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho = PASTA_MATERIAS / f"{timestamp}_{slug}.md"

    with open(caminho, "w", encoding="utf-8") as f:
        f.write(montar_markdown_final(artigo))

    marcar_tema_como_publicado(ideia.titulo)

    print(f"  -> salvo em {caminho}")
    return caminho


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(
        description="Gera matérias completas do SpiritCode a partir das ideias do gerador_temas.py"
    )
    parser.add_argument(
        "--arquivo", type=str, default=None,
        help="Caminho do .json de ideias. Se omitido, pega o mais recente em saidas/."
    )
    parser.add_argument(
        "--indices", type=str, default=None,
        help="Números das ideias a escrever, separados por vírgula (ex: 1,7). "
             "Se omitido, pergunta interativamente."
    )
    args = parser.parse_args()

    caminho_ideias = Path(args.arquivo) if args.arquivo else encontrar_arquivo_ideias_mais_recente()
    ideias = carregar_ideias(caminho_ideias)
    indices_escolhidos = escolher_indices(args.indices, ideias)

    ideias_por_numero = {ideia.numero: ideia for ideia in ideias}
    caminhos_gerados = [
        gerar_materia(ideias_por_numero[numero]) for numero in indices_escolhidos
    ]

    print(f"\n{len(caminhos_gerados)} matéria(s) gerada(s) com sucesso:")
    for c in caminhos_gerados:
        print(f"  - {c}")


if __name__ == "__main__":
    main()
