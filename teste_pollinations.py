import requests
from urllib.parse import quote

import config

PROMPT = "a dark server room with a single glowing blue monitor, volumetric haze, cinematic lighting"
MODELS = ["flux", "nanobanana", "zimage"]

if not config.POLLINATIONS_API_KEY:
    raise SystemExit("Falta POLLINATIONS_API_KEY no .env")

headers = {"Authorization": f"Bearer {config.POLLINATIONS_API_KEY}"}

print("Modelos de imagem listados pelo Pollinations:")
try:
    data = requests.get("https://gen.pollinations.ai/image/models", headers=headers, timeout=30).json()
    if isinstance(data, list):
        data = [m if isinstance(m, str) else (m.get("name") or m.get("id") or str(m)) for m in data]
    print(data)
except Exception as exc:
    print("Não consegui listar:", exc)

for model in MODELS:
    print(f"\n--- {model}")
    try:
        resp = requests.get(
            f"https://gen.pollinations.ai/image/{quote(PROMPT, safe='')}",
            params={"model": model, "width": 1280, "height": 720, "nologo": "true", "private": "true"},
            headers=headers,
            timeout=240,
        )
    except Exception as exc:
        print("Erro de conexão:", exc)
        continue
    kind = resp.headers.get("content-type", "")
    print("Status:", resp.status_code, "|", kind, "|", len(resp.content), "bytes")
    if resp.status_code == 200 and "image" in kind:
        path = f"teste_pollinations_{model}.jpg"
        open(path, "wb").write(resp.content)
        print("Salvo em", path)
    else:
        print("Resposta:", resp.text[:200])
