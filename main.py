import os
import io
import uuid
import asyncio
import requests
from datetime import datetime
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from PIL import Image
from concurrent.futures import ThreadPoolExecutor

app = FastAPI(title="Criativo API", version="2.0.0")

CLICKUP_TOKEN = os.environ.get("CLICKUP_TOKEN")
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "5"))

# Pool de threads para processar imagens em paralelo sem travar o event loop
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# Armazena status dos jobs em memória
# Em produção com muita escala, substituir por Redis
jobs: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Funções de processamento (rodam em thread separada)
# ---------------------------------------------------------------------------

def _compor_logo(imagem_bytes: bytes, logo_bytes: bytes) -> bytes:
    """Sobrepõe a logo no canto superior esquerdo da imagem."""
    imagem = Image.open(io.BytesIO(imagem_bytes)).convert("RGBA")
    logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")

    logo_max_w, logo_max_h = 260, 90
    logo.thumbnail((logo_max_w, logo_max_h), Image.LANCZOS)

    pos_x, pos_y = 45, 45

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

    # Upload do anexo
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

    # Comentário na task
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


def _processar_job(job_id: str, imagem_bytes: bytes, logo_bytes: bytes, task_id: str):
    """Função principal que roda em thread — compõe e publica."""
    try:
        jobs[job_id]["status"] = "processando"

        imagem_final = _compor_logo(imagem_bytes, logo_bytes)
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
        "servico": "Criativo API v2",
        "jobs_na_fila": sum(1 for j in jobs.values() if j["status"] == "processando"),
        "workers_disponiveis": MAX_WORKERS
    }


@app.post("/compor-criativo")
async def compor_criativo(
    imagem: UploadFile = File(...),
    logo: UploadFile = File(...),
    task_id: str = Form(...)
):
    """
    Recebe imagem + logo + task_id.
    Retorna job_id imediatamente e processa em background.
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

    # Dispara em thread separada — não bloqueia o n8n
    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        executor,
        _processar_job,
        job_id, imagem_bytes, logo_bytes, task_id
    )

    return JSONResponse(content={
        "sucesso": True,
        "job_id": job_id,
        "status": "na_fila",
        "mensagem": "Processamento iniciado. A imagem será publicada no ClickUp em instantes."
    })


@app.get("/status/{job_id}")
def status_job(job_id: str):
    """Consulta o status de um job específico."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    return JSONResponse(content=job)


@app.get("/jobs")
def listar_jobs():
    """Lista todos os jobs (útil para debug)."""
    return JSONResponse(content={
        "total": len(jobs),
        "jobs": list(jobs.values())
    })
