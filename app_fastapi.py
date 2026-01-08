# app_fastapi.py
# API PDF -> imágenes -> Groq Vision (Llama 4) -> JSON
# Estándar Nettalco/Planeamiento: FastAPI + Loguru + JSONResponse

import os
import json
import re
import base64
import io
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from loguru import logger

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel

from pdf2image import convert_from_path
from openai import OpenAI


# ===================== CONFIG =====================
load_dotenv()

logger.add(
    "serverAPI.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
    level="INFO",
    rotation="10 MB",
    compression="zip",
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
if not GROQ_API_KEY:
    raise SystemExit("❌ Falta GROQ_API_KEY en tu .env")

MODEL_VISION = os.getenv(
    "GROQ_VISION_MODEL",
    "meta-llama/llama-4-maverick-17b-128e-instruct"
).strip()

DPI = int(os.getenv("PDF_IMG_DPI", "200"))
MAX_PAGES = int(os.getenv("PDF_MAX_PAGES", "5"))
MAX_IMAGES_PER_REQUEST = 5

BASE_DIR = Path(__file__).resolve().parent
POPPLER_BIN = BASE_DIR / "poppler-25.12.0" / "Library" / "bin"

PDF_BASE_DIR = Path(os.getenv("PDF_BASE_DIR", "O:/Publicar_Web/Ordenes_Servicio"))

client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")


# ===================== APP =====================
app = FastAPI(
    title="Cotizacion Extractor API",
    description="PDF -> imágenes -> Groq Vision (Llama 4) -> JSON",
    version="1.0.0",
)


@app.on_event("startup")
async def startup():
    logger.info("🚀 API iniciada")
    logger.info(f"Modelo: {MODEL_VISION}")
    logger.info(f"PDF_BASE_DIR: {PDF_BASE_DIR}")
    logger.info(f"Existe base dir?: {PDF_BASE_DIR.exists()}")
    logger.info(f"Poppler path: {POPPLER_BIN}")


@app.on_event("shutdown")
async def shutdown():
    logger.info("🛑 API detenida")


# ===================== MODELS =====================

class ProcessRequest(BaseModel):
    pdf: str


# ===================== HELPERS =====================

def pdf_to_images(pdf_path: Path, dpi: int, max_pages: int):
    logger.info(f"Usando POPPLER_BIN: {POPPLER_BIN}")

    if not POPPLER_BIN.exists():
        raise RuntimeError(f"❌ Poppler NO encontrado en: {POPPLER_BIN}")

    try:
        images = convert_from_path(
            str(pdf_path),
            dpi=dpi,
            poppler_path=str(POPPLER_BIN)
        )
    except Exception as e:
        raise RuntimeError(f"❌ Error en convert_from_path: {e}")

    return images[:max_pages]


def _strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _safe_json_load(s: str) -> dict:
    s = _strip_code_fences(s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if not m:
            raise RuntimeError(f"Respuesta no-JSON del modelo:\n{s}")
        return json.loads(m.group(0))


def image_to_data_url(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


# ===================== PROMPTS =====================

BASE_RULES = """
Devuelve SOLO JSON con esta estructura exacta:
{
  "documento": {
        "ruc": string o null,
        "empresa": string o null,
        "codigo_factura": string o null,
        "fecha_emision": string o null,
        "moneda": string o null,
        "vigencia": string o null,
        "formato_pago": string o null,
        "igv": number o null
  },
  "items": {
        "columnas": ["nombre", "cantidad", "precio", "unidad"],
        "datos": [
            ["valor1", numero1, numero1, "unidad1"]
        ]
  }
}

Reglas:
- NO inventes datos.
- Copia el nombre COMPLETO tal como aparece en el documento.
- cantidad: si no aparece, null.
- precio: debe ser PRECIO UNITARIO, no total.
- unidad: UND, KG, M, M2, GLB, HH, DIA. Si no se ve, "UND".
- Sin markdown. Solo JSON válido.
""".strip()

CHUNK_RULES = """
Devuelve SOLO JSON con esta estructura exacta (SOLO items):
{
  "items": {
        "columnas": ["nombre", "cantidad", "precio", "unidad"],
        "datos": [
            ["valor1", numero1, numero1, "unidad1"]
        ]
  }
}

Reglas:
- NO inventes datos.
- Copia el nombre COMPLETO.
- precio es UNITARIO, no total.
- Sin markdown. Solo JSON válido.
""".strip()


# ===================== CORE =====================

def groq_vision_extract(images) -> Dict[str, Any]:
    final_doc = {
        "ruc": None,
        "empresa": None,
        "codigo_factura": None,
        "fecha_emision": None,
        "moneda": None,
        "vigencia": None,
        "formato_pago": None,
        "igv": None
    }

    final_items = {
        "columnas": ["nombre", "cantidad", "precio", "unidad"],
        "datos": []
    }

    batches = [images[i:i + MAX_IMAGES_PER_REQUEST] for i in range(0, len(images), MAX_IMAGES_PER_REQUEST)]
    logger.info(f"Procesando {len(images)} páginas en {len(batches)} batch(es)")

    for bi, batch in enumerate(batches):
        is_first = (bi == 0)
        prompt_text = BASE_RULES if is_first else CHUNK_RULES

        content = [{"type": "text", "text": prompt_text}]
        for img in batch:
            content.append({"type": "image_url", "image_url": {"url": image_to_data_url(img)}})

        resp = client.chat.completions.create(
            model=MODEL_VISION,
            messages=[
                {"role": "system", "content": "Eres un extractor de documentos. Responde SOLO JSON válido."},
                {"role": "user", "content": content},
            ],
            temperature=0.0,
        )

        raw = resp.choices[0].message.content or ""
        parsed = _safe_json_load(raw)

        if is_first and "documento" in parsed:
            for k in final_doc.keys():
                if parsed["documento"].get(k) is not None:
                    final_doc[k] = parsed["documento"][k]

        items = parsed.get("items")
        if isinstance(items, dict):
            datos = items.get("datos", [])
            if isinstance(datos, list):
                final_items["datos"].extend(datos)

    return {"documento": final_doc, "items": final_items}


# ===================== ENDPOINTS =====================

@app.get("/home", response_class=PlainTextResponse)
def home():
    return "OK - API en línea"


@app.post("/process", response_class=JSONResponse)
def process_pdf(req: ProcessRequest):
    try:
        pdf_name = Path(req.pdf).name
        if not pdf_name.lower().endswith(".pdf"):
            pdf_name += ".pdf"

        pdf_path = PDF_BASE_DIR / pdf_name

        logger.info("========== DEBUG PROCESS ==========")
        logger.info(f"PDF recibido        : {req.pdf}")
        logger.info(f"PDF normalizado     : {pdf_name}")
        logger.info(f"PDF_BASE_DIR        : {PDF_BASE_DIR}")
        logger.info(f"Ruta completa       : {pdf_path}")
        logger.info(f"Existe base dir?    : {PDF_BASE_DIR.exists()}")
        logger.info(f"Existe archivo?     : {pdf_path.exists()}")

        if not PDF_BASE_DIR.exists():
            raise RuntimeError(f"❌ La carpeta base NO existe: {PDF_BASE_DIR}")

        if not pdf_path.exists():
            raise RuntimeError(f"❌ El PDF NO existe: {pdf_path}")

        # --- PDF a imágenes ---
        logger.info("Convirtiendo PDF a imágenes...")
        images = pdf_to_images(pdf_path, dpi=DPI, max_pages=MAX_PAGES)
        logger.info(f"Imágenes generadas  : {len(images)}")

        if not images:
            raise RuntimeError("❌ pdf_to_images devolvió lista vacía")

        # --- Groq Vision ---
        logger.info("Enviando a Groq Vision...")
        data = groq_vision_extract(images)
        logger.info("Respuesta de Groq OK")

        return JSONResponse(content={
            "status": "ok",
            "debug": {
                "pdf_path": str(pdf_path),
                "images": len(images)
            },
            "data": data
        })

    except Exception as e:
        logger.error("🔥 ERROR REAL EN PROCESS 🔥", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error_type": type(e).__name__,
                "error_message": str(e)
            }
        )


# ===================== RUN =====================
# uvicorn app_fastapi:app --host 0.0.0.0 --port 5000 --reload
