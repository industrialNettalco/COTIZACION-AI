import os
import json
import re
import base64
import io
from pathlib import Path
from dotenv import load_dotenv

import requests
from flask import Flask, request, jsonify
from pdf2image import convert_from_path
from openai import OpenAI

# ===================== CONFIG =====================
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
if not GROQ_API_KEY:
    raise SystemExit("Falta GROQ_API_KEY en tu .env")

# MODELO GROQ QUE SOPORTA IMAGENES (Vision)
# Docs Groq: Llama 4 Scout / Maverick soportan imágenes y JSON mode
MODEL_VISION = os.getenv(
    "GROQ_VISION_MODEL",
    "meta-llama/llama-4-maverick-17b-128e-instruct"
).strip()

# PDF -> imágenes
DPI = int(os.getenv("PDF_IMG_DPI", "220"))
MAX_PAGES = int(os.getenv("PDF_MAX_PAGES", "10"))

# Límite Groq Vision: máximo 5 imágenes por request
MAX_IMAGES_PER_REQUEST = 5

# Poppler (Windows/Conda)
POPPLER_BIN = os.path.join(os.environ.get("CONDA_PREFIX", ""), "Library", "bin")

TMP_DIR = Path("tmp")
TMP_DIR.mkdir(exist_ok=True)

# Cliente OpenAI-compatible apuntando a GROQ
client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

app = Flask(__name__)

# ===================== HELPERS =====================

def download_pdf(url: str, out_path: Path) -> Path:
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)
    return out_path


def pdf_to_images(pdf_path: Path, dpi: int = 220, max_pages: int = 10):
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


def _move_nombre_overflow_to_adicional(items: dict, name_limit: int = 60, adicional1_limit: int = 60, adicional2_limit: int = 60):
    """Distribuye el overflow de `nombre` en `adicional1` y `adicional2`.

    Reglas:
    - `nombre` se corta a `name_limit`.
    - Cualquier overflow se coloca en `adicional1` hasta su límite.
    - Si sigue habiendo texto, va a `adicional2` hasta su límite.
    - Se preserva el contenido existente en `adicional1` y `adicional2` cuando sea posible.
    Modifica `items` in-place.
    """
    if not isinstance(items, dict):
        return
    columnas = items.get("columnas", [])
    datos = items.get("datos", [])

    # índices esperados en nuestro formato final
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
        # Asegurar longitud suficiente
        while len(row) <= idx_ad2:
            row.append(None)

        nombre = row[idx_nombre]
        if not isinstance(nombre, str):
            continue
        if len(nombre) <= name_limit:
            # nada que mover
            continue

        overflow = nombre[name_limit:].strip()
        row[idx_nombre] = nombre[:name_limit].strip()

        # Obtener existentes
        existing_ad1 = row[idx_ad1] if isinstance(row[idx_ad1], str) else ""
        existing_ad2 = row[idx_ad2] if isinstance(row[idx_ad2], str) else ""

        # Si existing_ad1 tiene ya >= limit, no le añadimos más; vamos directo a ad2
        ad1_space = max(0, adicional1_limit - len(existing_ad1.strip())) if adicional1_limit else 0

        to_ad1 = ""
        to_ad2 = ""

        if ad1_space > 0:
            # espacio para parte del overflow en ad1
            take = overflow[:ad1_space]
            to_ad1 = (existing_ad1.strip() + (" " + take.strip() if existing_ad1.strip() else take.strip())).strip()
            remaining = overflow[len(take):].strip()
        else:
            to_ad1 = existing_ad1.strip() or None
            remaining = overflow

        if remaining:
            # llenar ad2 hasta su límite
            to_ad2 = remaining[:adicional2_limit].strip() if adicional2_limit else remaining

        # Asegurar los límites finales
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

def groq_vision_extract(images) -> dict:
    """
    Procesa el PDF en batches de hasta 5 imágenes (limitación Groq)
    y devuelve el JSON final con:
    - documento (tomado del primer batch)
    - items (concatenando datos de todos los batches)
    """
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

    for bi, batch in enumerate(batches):
        is_first = (bi == 0)
        prompt_text = BASE_RULES if is_first else CHUNK_RULES

        content = [{"type": "text", "text": prompt_text}]
        for img in batch:
            # IMPORTANTE: image_url debe ser objeto {url: ...} según Groq docs
            content.append({
                "type": "image_url",
                "image_url": {"url": image_to_data_url(img)}
            })

        resp = client.chat.completions.create(
            model=MODEL_VISION,
            messages=[
                {"role": "system", "content": "Eres un extractor de documentos. Responde SOLO JSON válido, sin markdown."},
                {"role": "user", "content": content},
            ],
            temperature=0.0,
        )

        parsed = _safe_json_load(resp.choices[0].message.content)

        # Merge
        if is_first and isinstance(parsed, dict) and "documento" in parsed and isinstance(parsed["documento"], dict):
            for k in final_doc.keys():
                v = parsed["documento"].get(k, None)
                if v is not None:
                    final_doc[k] = v

        items = None
        if isinstance(parsed, dict) and "items" in parsed and isinstance(parsed["items"], dict):
            items = parsed["items"]

        if items:
            # columnas: respetar si vienen igual; si vienen diferentes, mantenemos las nuestras
            datos = items.get("datos", [])
            if isinstance(datos, list):
                # concatenar sin agrupar
                final_items["datos"].extend(datos)

    # Post-procesar items: mover overflow de `nombre` a `adicional1`/`adicional2` si corresponde
    _move_nombre_overflow_to_adicional(final_items, name_limit=60, adicional1_limit=60, adicional2_limit=60)

    return {"documento": final_doc, "items": final_items}


# ===================== ENDPOINTS =====================

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "message": "API PDF->imagenes->Groq Vision (Llama 4) -> JSON",
        "model": MODEL_VISION,
        "uso": "/process?pdf_url=<URL_DEL_PDF>"
    })


@app.route("/process", methods=["GET"])
def process_pdf():
    pdf_url = request.args.get("pdf_url")
    if not pdf_url:
        return jsonify({"error": "Falta el parámetro pdf_url"}), 400

    try:
        pdf_path = TMP_DIR / "input.pdf"
        download_pdf(pdf_url, pdf_path)

        images = pdf_to_images(pdf_path, dpi=DPI, max_pages=MAX_PAGES)
        if not images:
            return jsonify({"error": "No se pudieron generar imágenes del PDF."}), 500

        data = groq_vision_extract(images)
        return jsonify(data)

    except requests.RequestException as e:
        return jsonify({"error": f"Error descargando PDF: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
