import base64
import os
import time
from pathlib import Path
from urllib.parse import quote

import requests

import config
from utils import log, warn

CF_MODEL = os.getenv("IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
TOGETHER_MODEL = os.getenv("TOGETHER_MODEL", "black-forest-labs/FLUX.1-schnell")
POLL_MODELS = [m.strip() for m in os.getenv("POLLINATIONS_MODELS", "flux,nanobanana").split(",") if m.strip()]
POLL_WIDTH = int(os.getenv("POLLINATIONS_WIDTH", "1280"))
POLL_HEIGHT = int(os.getenv("POLLINATIONS_HEIGHT", "720"))
OVH_URL = "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/images/generations"
# A página da OVHcloud mostra dois nomes para o mesmo modelo; o código testa os dois.
OVH_MODELS = [
    m.strip()
    for m in os.getenv("OVH_MODELS", "stabilityai/stable-diffusion-xl-base-1.0,stable-diffusion-xl-base-v10").split(",")
    if m.strip()
]
# Sem chave a OVHcloud aceita 2 pedidos por minuto por IP; 31 s entre imagens fica abaixo do limite.
OVH_MIN_INTERVAL = float(os.getenv("OVH_MIN_INTERVAL_SECONDS", "31"))
DELAY = float(os.getenv("IMAGE_DELAY_SECONDS", "1"))
ATTEMPTS = 4

_bad_poll_models = set()
_bad_ovh_models = set()
_ovh_last_call = [0.0]


class QuotaError(RuntimeError):
    """Este provedor não serve mais hoje (limite, chave inválida ou sem modelo): passar para o próximo."""


def _json_or_none(resp):
    try:
        return resp.json()
    except ValueError:
        return None


def _extension(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


# ---------------------------------------------------------------- Cloudflare
def _cloudflare(prompt):
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{config.CLOUDFLARE_ACCOUNT_ID}"
        f"/ai/run/{CF_MODEL}"
    )
    headers = {"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"}
    if "klein" in CF_MODEL:
        fields = {"prompt": (None, prompt)}
        resp = requests.post(url, headers=headers, files=fields, timeout=180)
    else:
        resp = requests.post(url, headers=headers, json={"prompt": prompt, "steps": 4}, timeout=180)

    if "image" in resp.headers.get("content-type", ""):
        return resp.content
    data = _json_or_none(resp)
    if data and data.get("success") and (data.get("result") or {}).get("image"):
        return base64.b64decode(data["result"]["image"])
    text = str(data).lower()
    if "daily free allocation" in text or "4006" in text or "neurons" in text:
        raise QuotaError("Cloudflare: o limite diário grátis acabou.")
    raise RuntimeError(f"Cloudflare {resp.status_code}: {data or resp.text[:300]}")


# ------------------------------------------------------------------ Together
def _together(prompt):
    resp = requests.post(
        "https://api.together.xyz/v1/images/generations",
        headers={"Authorization": f"Bearer {config.TOGETHER_API_KEY}"},
        json={
            "model": TOGETHER_MODEL,
            "prompt": prompt,
            "width": 1344,
            "height": 768,
            "steps": 4,
            "n": 1,
            "response_format": "b64_json",
        },
        timeout=180,
    )
    data = _json_or_none(resp)
    try:
        return base64.b64decode(data["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError):
        text = str(data).lower()
        if resp.status_code in (401, 402, 403) or "credit" in text or "quota" in text:
            raise QuotaError(f"Together: sem crédito ou chave recusada ({resp.status_code}).")
        raise RuntimeError(f"Together {resp.status_code}: {data or resp.text[:300]}")


# -------------------------------------------------------------- Pollinations
def _pollinations(prompt):
    models = [m for m in POLL_MODELS if m not in _bad_poll_models]
    if not models:
        raise QuotaError("Pollinations: nenhum modelo disponível para a sua chave.")
    model = models[0]
    url = f"https://gen.pollinations.ai/image/{quote(prompt[:1500], safe='')}"
    resp = requests.get(
        url,
        params={
            "model": model,
            "width": POLL_WIDTH,
            "height": POLL_HEIGHT,
            "nologo": "true",
            "private": "true",
        },
        headers={"Authorization": f"Bearer {config.POLLINATIONS_API_KEY}"},
        timeout=240,
    )
    if resp.status_code == 200 and "image" in resp.headers.get("content-type", "") and len(resp.content) > 2000:
        return resp.content

    body = resp.text[:300].lower()
    if resp.status_code == 401:
        raise QuotaError("Pollinations: chave recusada (401). Confira o POLLINATIONS_API_KEY.")
    if resp.status_code == 429:
        time.sleep(float(resp.headers.get("retry-after", 20) or 20))
        raise RuntimeError("Pollinations: limite de velocidade (429), tentando de novo")
    unusable = resp.status_code in (400, 402, 403, 404) and any(
        word in body for word in ("model", "pollen", "balance", "tier", "insufficient", "not found")
    )
    if unusable:
        _bad_poll_models.add(model)
        warn(f"Pollinations: modelo '{model}' indisponível para a sua chave ({resp.status_code}). Tentando o próximo.")
        return _pollinations(prompt)
    raise RuntimeError(f"Pollinations {resp.status_code}: {resp.text[:200]}")


# ----------------------------------------------------------------------- OVH
def _ovh(prompt):
    """OVHcloud AI Endpoints (Stable Diffusion XL). Sem chave: 2 pedidos/min por IP."""
    models = [m for m in OVH_MODELS if m not in _bad_ovh_models]
    if not models:
        raise QuotaError("OVHcloud: nenhum nome de modelo foi aceito.")
    model = models[0]

    wait = OVH_MIN_INTERVAL - (time.time() - _ovh_last_call[0])
    if wait > 0:
        time.sleep(wait)

    headers = {"Content-Type": "application/json"}
    if config.OVH_API_KEY:
        headers["Authorization"] = f"Bearer {config.OVH_API_KEY}"
    resp = requests.post(
        OVH_URL,
        headers=headers,
        json={"model": model, "prompt": prompt[:1500], "size": "1024x1024"},
        timeout=240,
    )
    _ovh_last_call[0] = time.time()

    data = _json_or_none(resp)
    try:
        return base64.b64decode(data["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError):
        pass

    body = (resp.text or "")[:300].lower()
    if resp.status_code == 429:
        pause = min(float(resp.headers.get("retry-after", 35) or 35), 120)
        time.sleep(pause)
        raise RuntimeError("OVHcloud: limite de velocidade (429), tentando de novo")
    if resp.status_code in (401, 402, 403):
        raise QuotaError(f"OVHcloud: acesso recusado ({resp.status_code}).")
    if resp.status_code in (400, 404) and "model" in body:
        _bad_ovh_models.add(model)
        warn(f"OVHcloud: nome de modelo '{model}' não aceito ({resp.status_code}). Tentando o próximo.")
        return _ovh(prompt)
    raise RuntimeError(f"OVHcloud {resp.status_code}: {resp.text[:200]}")


PROVIDER_FUNCS = {"cloudflare": _cloudflare, "pollinations": _pollinations, "together": _together, "ovh": _ovh}


def _generate_one(prompt, active):
    """Tenta cada provedor ativo, na ordem. Quem esgota sai da lista até o fim da execução."""
    for name in list(active):
        func = PROVIDER_FUNCS[name]
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return name, func(prompt)
            except QuotaError as exc:
                warn(str(exc))
                active.remove(name)
                if active:
                    log(f"Passando para: {active[0]}")
                break
            except Exception as exc:
                warn(f"Imagem ({name}): tentativa {attempt}/{ATTEMPTS} falhou: {exc}")
                if attempt < ATTEMPTS:
                    time.sleep(10 * attempt)
    return None, None


def _existing(images_dir, index):
    for path in sorted(images_dir.glob(f"img_{index:03d}.*")):
        if path.stat().st_size > 0 and not path.name.endswith(".part"):
            return path
    return None


def generate_images(prompts, images_dir):
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    for name in config.IMAGE_PROVIDERS:
        if name not in config.usable_image_providers():
            warn(f"Provedor '{name}' está na lista, mas faltam chaves no .env. Ele será ignorado.")
    active = config.usable_image_providers()
    if not active:
        raise RuntimeError("Nenhum provedor de imagem com chaves preenchidas. Confira o .env.")
    log("Provedores de imagem, em ordem: " + " > ".join(active))

    paths, counts = [], {}
    total = len(prompts)

    for index, prompt in enumerate(prompts, start=1):
        existing = _existing(images_dir, index)
        if existing:
            paths.append(existing)
            continue

        label = active[0] if active else "-"
        log(f"Imagem {index}/{total} ({label})")
        name, data = _generate_one(prompt, active)

        if data is None and not active:
            raise SystemExit(
                "Todos os provedores de imagem atingiram o limite. As imagens já geradas ficaram salvas. "
                "Depois da meia-noite UTC (21h no Brasil) rode de novo com --project apontando "
                "para a pasta do projeto."
            )

        if data:
            path = images_dir / f"img_{index:03d}{_extension(data)}"
            path.write_bytes(data)
            counts[name] = counts.get(name, 0) + 1
        elif paths:
            warn(f"Imagem {index} falhou em todos os provedores; repetindo a anterior")
            path = images_dir / f"img_{index:03d}{paths[-1].suffix}"
            path.write_bytes(paths[-1].read_bytes())
        else:
            raise RuntimeError("A primeira imagem falhou. Confira as chaves do .env.")

        paths.append(path)
        time.sleep(DELAY)

    if counts:
        log("Imagens geradas por provedor: " + ", ".join(f"{k}: {v}" for k, v in counts.items()))
    return paths
