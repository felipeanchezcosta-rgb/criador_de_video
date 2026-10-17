"""
gerador_temas.py — SpiritCode
-------------------------------
Parte 1 da automação do blog "SpiritCode" (spiritcode.blog).

Responsabilidade deste arquivo (só isso, nada além):
  1. Consultar o arquivo temas_publicados.txt (histórico de tudo que já foi
     gerado antes) pra não repetir assunto.
  2. Montar um prompt pronto pedindo 20 ideias de temas/títulos de cauda longa e
     MUITO específicos (micro-nicho) sobre backend, DevOps, frontend e segurança,
     mirando desenvolvedores dos EUA.
  3. Enviar esse prompt pra API da Groq.
  4. Devolver e exibir a lista de 20 ideias (com tradução em português embaixo
     de cada título) e salvar em .json pra próxima etapa. Quem soma no
     temas_publicados.txt é o escritor_materias.py, só quando a matéria é
     realmente escrita — não toda ideia gerada aqui vira artigo.

Como rodar:
  pip install requests python-dotenv
  python gerador_temas.py
"""

import os
import json
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

QUANTIDADE_IDEIAS = 20

# -----------------------------------------------------------------------
# PROMPT — já pronto, o usuário não precisa escrever nada.
# Pensado pra fugir de temas genéricos ("what is docker", "intro to CORS")
# e ir direto no micro-nicho de cauda longa: o erro específico, o cenário
# específico que um dev encontra no dia a dia.
# -----------------------------------------------------------------------
SYSTEM_PROMPT = """You are a senior software engineer and SEO content strategist who \
writes for "SpiritCode", an English-language technical blog for US-based developers. \
You deeply understand backend, DevOps, frontend, and security topics, and you know the \
exact error messages, edge cases, and situational problems developers actually search \
for on Google when they're stuck.

Your task is to generate LONG-TAIL, MICRO-NICHE article ideas. No broad or generic \
topics. Each idea must address a very specific problem — the kind of thing a developer \
types into Google in the middle of debugging something real, not a topic that reads \
like an encyclopedia entry or an "intro to X" tutorial.

Mandatory rules for every idea:
- The title must be long-tail (8 to 14 words), phrased the way a developer would \
actually search — often including a specific error message, tool version, stack \
combination, or "why does X happen when Y" framing.
- The topic must be hyper-specific: a micro-niche within backend, DevOps, frontend, \
or security (e.g., a specific error in a specific framework/tool combo, a specific \
misconfiguration, a specific performance edge case, a specific migration scenario).
- Do not repeat the general topic of any article already published (provided below, \
when present).
- Do not generate generic titles like "what is X", "introduction to Y", "complete guide \
to Z".
- Each idea must clearly indicate its category: Backend, DevOps, Frontend, or Security.
- Write the title, category and micro-niche in English.
- Also provide "tituloPortugues": a natural, accurate Brazilian Portuguese translation
  of the title (not a literal word-for-word translation — it should read naturally to
  a Brazilian developer), for internal reference only.

You must respond ONLY with valid JSON, no text before or after, in this format:

{
  "ideias": [
    {
      "numero": 1,
      "vertente": "Backend | DevOps | Frontend | Security",
      "titulo": "long-tail, specific title, ready to use as the article's H1",
      "tituloPortugues": "natural Brazilian Portuguese translation of the title",
      "microNicho": "one short sentence explaining exactly what specific problem/scenario this solves",
      "intencaoBusca": "informacional | transacional | comparativa"
    }
  ]
}
"""

USER_PROMPT_TEMPLATE = """Generate {quantidade} new article ideas for the "SpiritCode" blog, \
following all the rules from the system prompt.

Topics/articles already published on the blog (DO NOT repeat these subjects, generate different ones):
{temas_ja_publicados}

Respond only with the requested JSON."""

# Lista curta de referência do que já foi publicado, só pra evitar repetição óbvia.
# Você pode ir editando essa lista conforme publica mais matérias.
TEMAS_JA_PUBLICADOS_INICIAL = [
    "AWS Lambda cold starts",
    "CORS errors in Node.js",
    "avoiding rate limits in Node.js",
    "Docker permission denied errors on Ubuntu",
    "optimizing Docker image size for Python microservices",
    "migrating legacy monoliths to AWS serverless architecture",
]

# Arquivo que guarda, um por linha, todos os títulos já gerados até hoje.
# Toda vez que o script gera novas ideias, ele já soma os títulos novos nesse
# arquivo sozinho — você não precisa mais editar a lista na mão.
ARQUIVO_TEMAS_PUBLICADOS = Path("temas_publicados.txt")


def carregar_temas_publicados() -> list[str]:
    """Lê o histórico de títulos já escritos como artigo de verdade. Na primeira
    vez que o script roda (arquivo ainda não existe), cria o arquivo com a
    lista inicial de exemplo."""
    if not ARQUIVO_TEMAS_PUBLICADOS.exists():
        ARQUIVO_TEMAS_PUBLICADOS.write_text(
            "\n".join(TEMAS_JA_PUBLICADOS_INICIAL) + "\n", encoding="utf-8"
        )
        return list(TEMAS_JA_PUBLICADOS_INICIAL)

    linhas = ARQUIVO_TEMAS_PUBLICADOS.read_text(encoding="utf-8").splitlines()
    return [linha.strip() for linha in linhas if linha.strip()]


def montar_prompt_usuario() -> str:
    temas_publicados = carregar_temas_publicados()
    temas_formatados = "\n".join(f"- {t}" for t in temas_publicados)
    return USER_PROMPT_TEMPLATE.format(
        quantidade=QUANTIDADE_IDEIAS,
        temas_ja_publicados=temas_formatados,
    )


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
        "temperature": 0.9,
        # O plano gratuito da Groq limita esse modelo a 8000 tokens por minuto,
        # contando prompt + reasoning + resposta. reasoning_effort "low" faz o
        # modelo "pensar menos" e sobrar mais tokens pra resposta em si.
        "max_tokens": 6500,
        "reasoning_effort": "low",
        "response_format": {"type": "json_object"},
    }

    resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)

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


def exibir_ideias(resultado: dict) -> None:
    ideias = resultado.get("ideias", [])
    print(f"\n{'=' * 70}")
    print(f"  {len(ideias)} IDEIAS DE MATÉRIA — SPIRITCODE")
    print(f"{'=' * 70}\n")
    for ideia in ideias:
        print(f"[{ideia.get('numero')}] ({ideia.get('vertente')}) {ideia.get('titulo')}")
        print(f"     Tradução: {ideia.get('tituloPortugues')}")
        print(f"     Micro-nicho: {ideia.get('microNicho')}")
        print(f"     Intenção de busca: {ideia.get('intencaoBusca')}\n")

    if len(ideias) < QUANTIDADE_IDEIAS:
        print(f"AVISO: pedi {QUANTIDADE_IDEIAS} ideias, mas só vieram {len(ideias)}. "
              f"Se isso persistir, aumente ainda mais o max_tokens no código.")


def salvar_ideias(resultado: dict) -> str:
    os.makedirs("saidas", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho = os.path.join("saidas", f"ideias_temas_{timestamp}.json")
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    return caminho


def main():
    user_prompt = montar_prompt_usuario()
    resultado = chamar_groq(SYSTEM_PROMPT, user_prompt)
    exibir_ideias(resultado)
    caminho_salvo = salvar_ideias(resultado)

    print(f"Ideias salvas em: {caminho_salvo}")
    print("(esse .json vai ser usado depois pela próxima etapa, que escreve as matérias — "
          "só quando uma matéria for escrita de verdade ela entra no histórico de temas)")


if __name__ == "__main__":
    main()
