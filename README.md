# criador_de_video

Pipeline em Python que gera vídeos de YouTube no estilo "dark documentary" (histórias de hacks, golpes, falências e fracassos de tecnologia), em inglês, a partir de um tema ou de um roteiro seu.

Feito para rodar em máquina modesta: **4 GB de RAM, sem GPU, Linux**.

## Como funciona

```
tema ─► roteiro (Groq) ─► prompts de imagem (Groq) ─► imagens ─► narração (Kokoro)
      ─► plano de edição ─► efeitos sonoros + legenda ─► render (FFmpeg) ─► final.mp4
```

| Etapa | Arquivo | O que faz |
|---|---|---|
| Temas | `themes.py` | Lista de histórias prontas e opção de digitar o seu tema |
| Roteiro | `story_writer.py`, `roteiro_proprio.py`, `script_cleaner.py` | Gera o roteiro por seções ou usa o seu, e limpa marcações de produção |
| Prompts de imagem | `image_prompts.py` | Define o estilo visual (sombrio, misto ou colorido) conforme a história |
| Imagens | `image_generator.py` | Cloudflare FLUX, Pollinations, Together e OVH, com troca automática quando um acaba |
| Narração | `narration.py`, `narrate_worker.py` | Kokoro (local), ElevenLabs ou OpenAI; roda em processo separado para liberar RAM |
| Edição | `edit_plan.py`, `video_editor.py` | Plano de cortes (~1 a cada 4 s) e render com FFmpeg direto |
| Som | `music.py`, `sfx.py` | Trilha da pasta `assets/music` e efeitos sintéticos gerados com numpy |
| Legenda | `subtitles.py` | `.ass` queimado no vídeo e `.srt` para o YouTube |
| Base | `config.py`, `groq_helper.py`, `utils.py` | Configuração via `.env`, escolha automática do modelo do Groq e utilitários |

## Requisitos

- Linux (testado em Linux Mint/Ubuntu), Python 3.10+
- FFmpeg com `ffprobe` (com o filtro `libass` se quiser legenda queimada)
- Uma chave do [Groq](https://console.groq.com) (grátis) e pelo menos um provedor de imagem

## Instalação

```bash
sudo apt update && sudo apt install -y ffmpeg python3-venv git
git clone https://github.com/SEU_USUARIO/criador_de_video.git
cd criador_de_video
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Preencha no `.env` pelo menos `GROQ_API_KEY` e as chaves do provedor de imagem escolhido. **O `.env` nunca vai para o GitHub.**

Se quiser música de fundo, coloque arquivos `.mp3`, `.wav`, `.m4a`, `.ogg` ou `.flac` em `assets/music/`. Sem arquivos, o programa gera uma trilha ambiente sintética. A pasta não vem no repositório por causa de direitos autorais.

## Uso

```bash
cd criador_de_video && source venv/bin/activate && python main.py
```

Retomar um projeto interrompido (as imagens e áudios já prontos são reaproveitados):

```bash
python main.py --project output/NOME_DA_PASTA
```

Ver os modelos disponíveis na sua conta do Groq:

```bash
python listar_modelos.py
```

Testar provedores de imagem isoladamente:

```bash
python teste_ovh.py
python teste_pollinations.py
```

Para usar o seu próprio roteiro, veja `exemplos/roteiro_exemplo.txt`: só o texto da narração, em inglês, sem marcações de produção.

## Configuração

Tudo fica no `.env` (veja `.env.example`, que lista todas as variáveis com comentários). Alguns pontos:

- `IMAGE_PROVIDERS`: ordem dos provedores de imagem; quando o limite diário de um acaba, passa para o próximo.
- `NARRATION_PROVIDER`: `kokoro_onnx` (padrão, ~600 MB de RAM), `kokoro`, `elevenlabs` ou `openai`.
- `RENDER_WORKERS`: cortes renderizados em paralelo. Com pouca RAM, use 1 ou 2.
- `GROQ_MODEL` / `GROQ_STORY_MODEL`: deixe vazio para escolha automática.

Os limites gratuitos dos provedores mudam com frequência; confira nos sites de cada um.

## Segurança

- Chaves de API ficam só no `.env`, que está no `.gitignore`.
- Se uma chave for exposta (print, commit, chat), revogue e gere outra no painel do provedor.
- Os vídeos, imagens, áudios e modelos baixados ficam em `output/` e `assets/models/`, fora do Git.

## Aviso

Os roteiros gerados por IA podem conter erros de fato. Revise datas, números e afirmações antes de publicar, principalmente em histórias reais envolvendo pessoas.

## Licença

MIT. Veja `LICENSE`.
