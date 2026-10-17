#!/usr/bin/env bash
# Prepara e sobe o projeto no GitHub com verificações de segurança.
# Uso:  ./publicar_github.sh [nome_do_repo] [private|public]
# Padrão: nome = nome da pasta, visibilidade = private (mude para public depois, se quiser).
set -euo pipefail

NOME="${1:-$(basename "$PWD")}"
VIS="${2:-private}"
[[ "$VIS" == "private" || "$VIS" == "public" ]] || { echo "Segundo argumento: private ou public"; exit 1; }
[[ -f config.py ]] || { echo "ERRO: rode dentro da pasta do projeto (onde está o config.py)."; exit 1; }

echo "==> 1/6 Ajustando arquivos"
# Linux diferencia maiúsculas: o código manda rodar 'listar_modelos.py'
if [[ -f Listar_modelos.py && ! -f listar_modelos.py ]]; then mv Listar_modelos.py listar_modelos.py; fi
if [[ -f Listar_modelos.py && -f listar_modelos.py ]]; then rm Listar_modelos.py; fi
mkdir -p assets/music exemplos; touch assets/music/.gitkeep
if [[ -f gary-mckinnon-nasa-hack-alien-search-en_1_.txt ]]; then
  mv gary-mckinnon-nasa-hack-alien-search-en_1_.txt exemplos/roteiro_exemplo.txt
fi

echo "==> 2/6 Conferindo ferramentas"
command -v git >/dev/null || { echo "Instale o git: sudo apt install -y git"; exit 1; }
if ! git config --global user.name >/dev/null; then
  read -rp "Seu nome para os commits: " N; git config --global user.name "$N"
fi
if ! git config --global user.email >/dev/null; then
  echo "Dica: use o e-mail noreply do GitHub (Settings > Emails) para não expor o seu."
  read -rp "E-mail para os commits: " E; git config --global user.email "$E"
fi

echo "==> 3/6 Iniciando o repositório"
[[ -d .git ]] || git init -q -b main
git add -A

echo "==> 4/6 Verificação de segurança"
FALHOU=0
if git ls-files --error-unmatch .env >/dev/null 2>&1; then echo "ERRO: .env entrou no Git!"; FALHOU=1; fi
# arquivos que nunca devem subir
PROIBIDOS=$(git ls-files | grep -Ei '(^|/)(\.env$|venv/|output/|assets/models/)|\.(mp4|wav|mp3|pem|key)$' | grep -v 'assets/music/.gitkeep' || true)
if [[ -n "$PROIBIDOS" ]]; then echo "ERRO: arquivos que não devem subir:"; echo "$PROIBIDOS"; FALHOU=1; fi
# arquivos grandes (limite do GitHub: 100 MB)
GRANDES=$(git ls-files -z | xargs -0 -r du -k 2>/dev/null | awk '$1>20000{print $2" ("int($1/1024)" MB)"}' || true)
if [[ -n "$GRANDES" ]]; then echo "ERRO: arquivos grandes demais:"; echo "$GRANDES"; FALHOU=1; fi
# padrões de chaves nos arquivos que vão subir (.env.example é ignorado: só tem campos vazios)
PADRAO='(gsk_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|(API_KEY|API_TOKEN|SECRET|PASSWORD|TOKEN)[A-Z_]*[[:space:]]*[=:][[:space:]]*["'"'"']?[A-Za-z0-9_\-]{16,})'
ACHADOS=$(git ls-files -z | xargs -0 -r grep -InE "$PADRAO" 2>/dev/null | grep -v '^\.env\.example:' || true)
if [[ -n "$ACHADOS" ]]; then
  echo "ERRO: possível chave dentro do código (valor oculto abaixo):"
  echo "$ACHADOS" | sed -E 's/(:[0-9]+:).*/\1 [linha suspeita]/'
  FALHOU=1
fi
if [[ $FALHOU -ne 0 ]]; then
  echo; echo "Nada foi enviado. Corrija e rode de novo. Se uma chave real apareceu, revogue e gere outra."
  git reset -q; exit 1
fi
echo "Tudo certo: nenhuma chave, .env, vídeo ou modelo na lista de envio."

echo "==> 5/6 Commit"
if git diff --cached --quiet; then echo "Nada novo para commitar."; else git commit -q -m "Initial commit: pipeline de vídeos dark documentary"; fi

echo "==> 6/6 Enviando para o GitHub ($VIS)"
if ! command -v gh >/dev/null; then
  echo "Instalando o GitHub CLI..."
  sudo apt update -qq && sudo apt install -y gh
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "Login no GitHub: escolha GitHub.com > HTTPS > Login with a web browser (não precisa colar token)."
  gh auth login
fi
if git remote get-url origin >/dev/null 2>&1; then
  git push -u origin main
else
  gh repo create "$NOME" "--$VIS" --source . --remote origin --push
fi
echo; echo "Pronto: $(gh repo view --json url -q .url)"
