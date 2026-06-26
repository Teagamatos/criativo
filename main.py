import os
import io
import uuid
import asyncio
import requests
from datetime import datetime
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image
from concurrent.futures import ThreadPoolExecutor

app = FastAPI(title="Criativo API", version="2.1.0")

CLICKUP_TOKEN = os.environ.get("CLICKUP_TOKEN")
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "5"))

executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
jobs: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Funções de processamento (rodam em thread separada)
# ---------------------------------------------------------------------------

def _remover_fundo_claro(logo: Image.Image, tolerancia: int = 235) -> Image.Image:
    """
    Remove pixels brancos/claros do fundo da logo, deixando transparente.
    tolerancia: valor de 0-255. Quanto maior, mais tons claros remove (235 = bem permissivo só com branco puro/quase puro).
    """
    logo = logo.convert("RGBA")
    dados = logo.getdata()

    novos_dados = []
    for r, g, b, a in dados:
        # Se o pixel for "claro o suficiente" em todos os canais, vira transparente
        if r >= tolerancia and g >= tolerancia and b >= tolerancia:
            novos_dados.append((r, g, b, 0))
        else:
            novos_dados.append((r, g, b, a))

    logo.putdata(novos_dados)
    return logo


def _compor_logo(imagem_bytes: bytes, logo_bytes: bytes, remover_fundo: bool = False) -> bytes:
    """Sobrepõe a logo no canto superior esquerdo da imagem, com fundo removido."""
    imagem = Image.open(io.BytesIO(imagem_bytes)).convert("RGBA")
    logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")

    if remover_fundo:
        logo = _remover_fundo_claro(logo, tolerancia=235)

    # Box branco: x, y, largura, altura
    box_x, box_y, box_w, box_h = 40, 40, 500, 160

    # Força redimensionar a logo para ocupar o box (com margem interna de 20px)
    largura_alvo = box_w - 20
    altura_alvo = box_h - 20

    # Mantém a proporção, mas usa o maior tamanho possível dentro do box
    # Limita a ampliação a 2x o tamanho original para não borrar logos pequenas
    proporcao = min(largura_alvo / logo.width, altura_alvo / logo.height)
    proporcao = min(proporcao, 2.0)
    novo_w = int(logo.width * proporcao)
    novo_h = int(logo.height * proporcao)
    logo = logo.resize((novo_w, novo_h), Image.LANCZOS)

    # Centraliza dentro do box
    pos_x = box_x + (box_w - novo_w) // 2
    pos_y = box_y + (box_h - novo_h) // 2

    camada = Image.new("RGBA", imagem.size, (0, 0, 0, 0))
    camada.paste(logo, (pos_x, pos_y), logo)

    resultado = Image.alpha_composite(imagem, camada).convert("RGB")

    buffer = io.BytesIO()
    resultado.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _publicar_clickup(task_id: str, imagem_bytes: bytes) -> dict:
    """Faz upload da imagem e publica comentário na task do ClickUp."""
    if not CLICKUP_TOKEN:
        raise Exception("CLICKUP_TOKEN não configurado.")

    headers = {"Authorization": CLICKUP_TOKEN}

    upload_url = f"https://api.clickup.com/api/v2/task/{task_id}/attachment"
    resp = requests.post(
        upload_url,
        headers=headers,
        files={"attachment": ("criativo_final.jpg", imagem_bytes, "image/jpeg")},
        timeout=30
    )
    if resp.status_code not in (200, 201):
        raise Exception(f"Erro upload ClickUp: {resp.status_code} - {resp.text}")

    anexo_url = resp.json().get("url", "")

    comment_url = f"https://api.clickup.com/api/v2/task/{task_id}/comment"
    requests.post(
        comment_url,
        headers={**headers, "Content-Type": "application/json"},
        json={
            "comment_text": "🖼️ Criativo gerado automaticamente com logo da empresa.",
            "notify_all": False
        },
        timeout=15
    )

    return {"anexo_url": anexo_url}


def _processar_job(job_id: str, imagem_bytes: bytes, logo_bytes: bytes, task_id: str, remover_fundo: bool):
    """Função principal que roda em thread — compõe e publica."""
    try:
        jobs[job_id]["status"] = "processando"

        imagem_final = _compor_logo(imagem_bytes, logo_bytes, remover_fundo)
        resultado = _publicar_clickup(task_id, imagem_final)

        jobs[job_id].update({
            "status": "concluido",
            "anexo_url": resultado["anexo_url"],
            "finalizado_em": datetime.utcnow().isoformat()
        })

    except Exception as e:
        jobs[job_id].update({
            "status": "erro",
            "erro": str(e),
            "finalizado_em": datetime.utcnow().isoformat()
        })


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def health():
    return {
        "status": "ok",
        "servico": "Criativo API v2.1",
        "jobs_na_fila": sum(1 for j in jobs.values() if j["status"] == "processando"),
        "workers_disponiveis": MAX_WORKERS
    }


@app.post("/compor-criativo")
async def compor_criativo(
    imagem: UploadFile = File(...),
    logo: UploadFile = File(...),
    task_id: str = Form(...),
    remover_fundo: bool = Form(False)
):
    """
    Recebe imagem + logo + task_id.
    Remove o fundo claro da logo (opcional) e processa em background.
    """
    imagem_bytes = await imagem.read()
    logo_bytes = await logo.read()

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "job_id": job_id,
        "task_id": task_id,
        "status": "na_fila",
        "criado_em": datetime.utcnow().isoformat(),
        "finalizado_em": None,
        "anexo_url": None,
        "erro": None
    }

    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        executor,
        _processar_job,
        job_id, imagem_bytes, logo_bytes, task_id, remover_fundo
    )

    return JSONResponse(content={
        "sucesso": True,
        "job_id": job_id,
        "status": "na_fila",
        "mensagem": "Processamento iniciado. A imagem será publicada no ClickUp em instantes."
    })


@app.get("/status/{job_id}")
def status_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    return JSONResponse(content=job)


@app.get("/jobs")
def listar_jobs():
    return JSONResponse(content={
        "total": len(jobs),
        "jobs": list(jobs.values())
    })