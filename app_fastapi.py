# app_fastapi.py
# API PDF -> imágenes -> Groq Vision (Llama 4) -> JSON
# Estándar Nettalco/Planeamiento: FastAPI + Loguru + HTTPException + JSONResponse + Startup/Shutdown

import os
import json
import re
import base64
import io
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from loguru import logger

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse, JSONResponse, Response

from pdf2image import convert_from_path
from openai import OpenAI


# ===================== CONFIG =====================
load_dotenv()

# ---- Logging corporativo ----
logger.add(
    "serverAPI.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
    level="INFO",
    rotation="10 MB",
    compression="zip",
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
if not GROQ_API_KEY:
    raise SystemExit("Falta GROQ_API_KEY en tu .env")

MODEL_VISION = os.getenv(
    "GROQ_VISION_MODEL",
    "meta-llama/llama-4-maverick-17b-128e-instruct"
).strip()

DPI = int(os.getenv("PDF_IMG_DPI"))
MAX_PAGES = int(os.getenv("PDF_MAX_PAGES"))
MAX_IMAGES_PER_REQUEST = 5

POPPLER_BIN = os.path.join(os.environ.get("CONDA_PREFIX", ""), "Library", "bin")

PDF_BASE_DIR = Path(os.getenv("PDF_BASE_DIR"))
TMP_DIR = Path("tmp")
TMP_DIR.mkdir(exist_ok=True)

# Cliente OpenAI-compatible apuntando a GROQ
client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")


# ===================== APP =====================
app = FastAPI(
    title="Cotizacion Extractor API",
    description="PDF -> imágenes -> Groq Vision (Llama 4) -> JSON",
    version="1.0.0",
)


@app.on_event("startup")
async def startup():
    # En tu estándar, se acostumbra tener startup/shutdown aunque aquí no haya DB async.
    logger.info("🚀 API iniciada")
    logger.info(f"Modelo: {MODEL_VISION}")
    logger.info(f"PDF_BASE_DIR: {PDF_BASE_DIR}")


@app.on_event("shutdown")
async def shutdown():
    logger.info("🛑 API detenida")


# ===================== HELPERS =====================

def pdf_to_images(pdf_path: Path, dpi: int, max_pages: int):
    if not POPPLER_BIN or not Path(POPPLER_BIN).exists():
        raise RuntimeError(
            f"Poppler no encontrado en {POPPLER_BIN}. "
            "Instala con: conda install -c conda-forge poppler"
        )
    images = convert_from_path(str(pdf_path), dpi=dpi, poppler_path=POPPLER_BIN)
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


def _move_nombre_overflow_to_adicional(
    items: Dict[str, Any],
    name_limit: int = 60,
    adicional1_limit: int = 60,
    adicional2_limit: int = 60
) -> None:
    if not isinstance(items, dict):
        return

    columnas = items.get("columnas", [])
    datos = items.get("datos", [])

    try:
        idx_nombre = columnas.index("nombre")
    except ValueError:
        idx_nombre = 0
    try:
        idx_ad1 = columnas.index("adicional1")
    except ValueError:
        idx_ad1 = 4
    try:
        idx_ad2 = columnas.index("adicional2")
    except ValueError:
        idx_ad2 = idx_ad1 + 1

    for row in datos:
        if not isinstance(row, list):
            continue

        while len(row) <= idx_ad2:
            row.append(None)

        nombre = row[idx_nombre]
        if not isinstance(nombre, str) or len(nombre) <= name_limit:
            continue

        overflow = nombre[name_limit:].strip()
        row[idx_nombre] = nombre[:name_limit].strip()

        existing_ad1 = row[idx_ad1] if isinstance(row[idx_ad1], str) else ""
        existing_ad2 = row[idx_ad2] if isinstance(row[idx_ad2], str) else ""

        ad1_space = max(0, adicional1_limit - len(existing_ad1.strip())) if adicional1_limit else 0

        if ad1_space > 0:
            take = overflow[:ad1_space]
            to_ad1 = (existing_ad1.strip() + (" " + take.strip() if existing_ad1.strip() else take.strip())).strip()
            remaining = overflow[len(take):].strip()
        else:
            to_ad1 = existing_ad1.strip() or None
            remaining = overflow

        to_ad2 = remaining[:adicional2_limit].strip() if remaining else None

        if isinstance(to_ad1, str) and adicional1_limit and len(to_ad1) > adicional1_limit:
            to_ad1 = to_ad1[:adicional1_limit].strip()
        if isinstance(to_ad2, str) and adicional2_limit and len(to_ad2) > adicional2_limit:
            to_ad2 = to_ad2[:adicional2_limit].strip()

        row[idx_ad1] = to_ad1 if to_ad1 else None
        row[idx_ad2] = to_ad2 if to_ad2 else None


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
        "columnas": ["nombre", "cantidad", "precio", "unidad", "adicional1", "adicional2"],
        "datos": [
            ["valor1", numero1, numero1, "unidad1", "info extra o null", "info extra 2 o null"]
        ]
  }
}

Reglas para HEADER:
- ruc: busca el RUC del EMISOR/PROVEEDOR. IMPORTANTE: El RUC 20100064571 es de NETTALCO (nosotros), NUNCA lo pongas como respuesta.
  Si solo encuentras ese RUC, usa null.
- empresa: nombre del proveedor/emisor. NO pongas NETTALCO.
- codigo_factura: numero de factura / boleta / cotizacion (ej "F001-123456", "JD0007853", "FAC-000123"). Busca etiquetas como "Factura", "Boleta", "Invoice", "Cotizacion", "No.", "N°", "Serie". Si no existe, null.
- fecha_emision: fecha de emisión del documento (ej "2023-12-31" o "31/12/2023"). Si no se puede determinar, usa null.
- moneda: si dice "S/" o "soles" => "PEN". Si "$" o "dolares" => "USD". Si no, null.
- vigencia: "validez", "vigencia", "oferta valida X dias", etc. Si no, null.
- formato_pago: contado / credito / 30 dias / 60 dias / contra entrega / adelanto. Si no, null.
- igv: MONTO en dinero (ej 180.00). NO porcentaje. Si no, null.

Reglas para ITEMS:
- nombre: NO inventes. Copia EXACTO del documento. Si no hay nombre, no incluyas item.
- cantidad: NO inventes. Si no aparece, null. IGNORA columna "ITEM" o "Nro" (solo numeración).
- precio: NO inventes. Debe ser PRECIO UNITARIO (P.Unit / Valor Unitario). NO total.
- unidad: UND, KG, M, M2, GLB, HH, DIA, etc. Si no se ve, "UND".
-- adicional1/adicional2: si el nombre supera 60 chars o hay info extra (códigos, nro máquina), ponlo aquí.
    Usa `adicional1` primero (máx 60). Si no cabe, usa `adicional2` (máx 60). Si no, null.

Reglas generales:
- Numeros con punto decimal.
- Sin markdown. Solo JSON válido.
- MAXIMO 60 caracteres en nombre. Extra va en adicional.
- NO agrupes items similares: cada fila del documento => una fila en datos.
""".strip()

CHUNK_RULES = """
Devuelve SOLO JSON con esta estructura exacta (SOLO items, sin documento):
{
  "items": {
        "columnas": ["nombre", "cantidad", "precio", "unidad", "adicional1", "adicional2"],
        "datos": [
            ["valor1", numero1, numero1, "unidad1", "info extra o null", "info extra 2 o null"]
        ]
  }
}

Reglas para ITEMS:
- nombre: NO inventes. Copia EXACTO del documento. Si no hay nombre, no incluyas item.
- cantidad: NO inventes. Si no aparece, null. IGNORA columna "ITEM" o "Nro" (solo numeración).
- precio: NO inventes. Debe ser PRECIO UNITARIO (P.Unit / Valor Unitario). NO total.
- unidad: UND, KG, M, M2, GLB, HH, DIA, etc. Si no se especifica, "UND".
- adicional1/adicional2: info extra max 60 cada uno. Usa `adicional1` primero, luego `adicional2` si hace falta.
- NO agrupes items similares.

Reglas generales:
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
        "columnas": ["nombre", "cantidad", "precio", "unidad", "adicional1", "adicional2"],
        "datos": []
    }

    batches = [images[i:i + MAX_IMAGES_PER_REQUEST] for i in range(0, len(images), MAX_IMAGES_PER_REQUEST)]
    logger.info(f"Procesando {len(images)} páginas en {len(batches)} batch(es) (máx {MAX_IMAGES_PER_REQUEST} imgs/request)")

    for bi, batch in enumerate(batches):
        is_first = (bi == 0)
        prompt_text = BASE_RULES if is_first else CHUNK_RULES

        content = [{"type": "text", "text": prompt_text}]
        for img in batch:
            content.append({"type": "image_url", "image_url": {"url": image_to_data_url(img)}})

        try:
            resp = client.chat.completions.create(
                model=MODEL_VISION,
                messages=[
                    {"role": "system", "content": "Eres un extractor de documentos. Responde SOLO JSON válido, sin markdown."},
                    {"role": "user", "content": content},
                ],
                temperature=0.0,
            )
        except Exception as e:
            logger.error(f"Error llamando Groq: {e}", exc_info=True)
            raise

        raw = resp.choices[0].message.content or ""
        parsed = _safe_json_load(raw)

        if is_first and isinstance(parsed, dict) and "documento" in parsed and isinstance(parsed["documento"], dict):
            for k in final_doc.keys():
                v = parsed["documento"].get(k, None)
                if v is not None:
                    final_doc[k] = v

        items = parsed.get("items") if isinstance(parsed, dict) else None
        if isinstance(items, dict):
            datos = items.get("datos", [])
            if isinstance(datos, list):
                final_items["datos"].extend(datos)

    _move_nombre_overflow_to_adicional(final_items, name_limit=60, adicional1_limit=60, adicional2_limit=60)
    return {"documento": final_doc, "items": final_items}


# ===================== ENDPOINTS (estilo empresa) =====================

@app.get("/home", response_class=PlainTextResponse)
def home():
    return "¡Bienvenido a la API!"


@app.get("/.well-known/appspecific/com.chrome.devtools.json")
def chrome_devtools():
    # DevTools hace este request automáticamente; responder 204 para evitar 404 en logs
    return Response(status_code=204)


@app.get("/", response_class=JSONResponse)
def index():
    return JSONResponse(content={
        "message": "API PDF->imagenes->Groq Vision (Llama 4) -> JSON",
        "model": MODEL_VISION,
        "uso": r"/process?pdf=<NOMBRE_DEL_PDF>  (busca en O:\Publicar_Web\Ordenes_Servicio)"
    })


@app.get("/process", response_class=JSONResponse)
def process_pdf(
    pdf: str = Query(..., description=r"Nombre del PDF en O:\Publicar_Web\Ordenes_Servicio")
):
    try:
        # evitar path traversal — usar solo el nombre del archivo
        pdf_name = Path(pdf).name
        if not pdf_name.lower().endswith(".pdf"):
            pdf_name += ".pdf"

        pdf_path = PDF_BASE_DIR / pdf_name
        if not pdf_path.exists():
            logger.warning(f"PDF no encontrado: {pdf_path}")
            raise HTTPException(status_code=400, detail=f"PDF no encontrado en {str(pdf_path)}")

        logger.info(f"Procesando PDF: {pdf_path.name}")

        images = pdf_to_images(pdf_path, dpi=DPI, max_pages=MAX_PAGES)
        if not images:
            logger.error("No se pudieron generar imágenes del PDF")
            raise HTTPException(status_code=500, detail="No se pudieron generar imágenes del PDF.")

        data = groq_vision_extract(images)
        return JSONResponse(content=data)

    except HTTPException:
        # ya es error controlado
        raise
    except Exception as e:
        logger.error(f"Error procesando PDF: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error interno al procesar el PDF")


# ===================== RUN =====================
# uvicorn app_fastapi:app --host 0.0.0.0 --port 5000 --reload
