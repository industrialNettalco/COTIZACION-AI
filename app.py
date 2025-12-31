"""
API REST para extracción de items desde PDFs de cotizaciones.

Este servicio descarga un PDF desde una URL, extrae el texto (usando OCR si es necesario),
y utiliza el modelo LLaMA 3.3 70B de GROQ para identificar y estructurar los items
(nombre, cantidad, precio) en formato JSON.

Endpoints:
    GET /           - Información de la API
    GET /process    - Procesa un PDF y extrae los items

Uso:
    GET /process?pdf_url=https://ejemplo.com/cotizacion.pdf

Dependencias externas:
    - GROQ_API_KEY en archivo .env
    - Poppler (para conversión PDF a imagen en OCR)
    - Tesseract (para OCR)
"""

import os
import json
import re
from pathlib import Path
from dotenv import load_dotenv

import requests
import pdfplumber
from pdf2image import convert_from_path
import pytesseract

from openai import OpenAI
from flask import Flask, request, jsonify

# Cargar variables de entorno desde .env
load_dotenv()

# ===================== CONFIGURACIÓN DE API =====================
api_key = os.getenv("GROQ_API_KEY", "").strip()
if not api_key:
    raise SystemExit("Falta GROQ_API_KEY en tu .env")

# Cliente OpenAI compatible apuntando a GROQ
client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

# ===================== CONFIGURACIÓN GENERAL =====================
MODEL = "llama-3.3-70b-versatile"  # Modelo de GROQ a utilizar
DPI = 300                          # Resolución para conversión PDF -> imagen (OCR)
LANG_OCR = "spa"                   # Idioma para Tesseract (spa=español, eng=inglés)

# Ruta a binarios de Poppler (requerido para pdf2image en Windows/Conda)
POPPLER_BIN = os.path.join(os.environ.get("CONDA_PREFIX", ""), "Library", "bin")

# Directorio temporal para almacenar PDFs descargados
TMP_DIR = Path("tmp")
TMP_DIR.mkdir(exist_ok=True)
# =================================================================

app = Flask(__name__)


# ===================== FUNCIONES DE DESCARGA =====================

def download_pdf(url: str, out_path: Path) -> Path:
    """
    Descarga un PDF desde una URL y lo guarda localmente.

    Args:
        url: URL directa al archivo PDF
        out_path: Ruta local donde guardar el archivo

    Returns:
        Path al archivo descargado

    Raises:
        requests.RequestException: Si hay error en la descarga
    """
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)
    return out_path


# ===================== FUNCIONES DE EXTRACCIÓN DE TEXTO =====================

def extract_text_pdfplumber(pdf_path: Path) -> str:
    """
    Extrae texto de un PDF usando pdfplumber (método rápido).

    Funciona bien con PDFs que tienen texto seleccionable/copiable.
    No funciona con PDFs escaneados o basados en imágenes.

    Args:
        pdf_path: Ruta al archivo PDF

    Returns:
        Texto extraído de todas las páginas concatenado
    """
    parts = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def extract_text_ocr(pdf_path: Path, dpi: int = DPI) -> str:
    """
    Extrae texto de un PDF usando OCR (Tesseract).

    Convierte cada página del PDF a imagen y luego aplica OCR.
    Más lento pero funciona con PDFs escaneados.

    Args:
        pdf_path: Ruta al archivo PDF
        dpi: Resolución para la conversión (mayor = mejor calidad pero más lento)

    Returns:
        Texto extraído via OCR de todas las páginas

    Raises:
        RuntimeError: Si Poppler no está instalado/configurado
    """
    if not POPPLER_BIN or not Path(POPPLER_BIN).exists():
        raise RuntimeError(
            f"Poppler no encontrado en {POPPLER_BIN}. "
            "Instala con: conda install -c conda-forge poppler"
        )

    # Convertir PDF a lista de imágenes (una por página)
    images = convert_from_path(str(pdf_path), dpi=dpi, poppler_path=POPPLER_BIN)

    # Aplicar OCR a cada imagen
    parts = []
    for img in images:
        parts.append(pytesseract.image_to_string(img, lang=LANG_OCR))
    return "\n".join(parts).strip()


def is_text_sufficient(text: str) -> bool:
    """
    Determina si el texto extraído es suficiente y válido para una cotización.

    Verifica:
    - Longitud mínima (150 caracteres)
    - Presencia de números (al menos 2% dígitos)
    - Palabras clave típicas de cotizaciones

    Args:
        text: Texto a evaluar

    Returns:
        True si el texto parece ser una cotización válida, False si necesita OCR
    """
    if not text:
        return False
    t = text.strip()

    # Muy corto = probablemente no extrajo bien
    if len(t) < 150:
        return False

    # Las cotizaciones tienen números (precios, cantidades, RUC, etc.)
    digit_ratio = sum(c.isdigit() for c in t) / max(len(t), 1)
    if digit_ratio < 0.02:
        return False

    # Debe contener al menos una palabra clave de cotización
    keywords = ["total", "subtotal", "igv", "precio", "cantidad", "s/", "usd", "ruc"]
    if not any(k in t.lower() for k in keywords):
        return False

    return True


def extract_text_smart(pdf_path: Path):
    """
    Extracción inteligente: usa pdfplumber primero, OCR solo si es necesario.

    Estrategia:
    1. Intentar extracción rápida con pdfplumber
    2. Validar si el texto es suficiente (is_text_sufficient)
    3. Si no es suficiente, usar OCR como fallback

    Args:
        pdf_path: Ruta al archivo PDF

    Returns:
        Tupla (texto_extraído, método_usado)
        método_usado: "PDF_TEXT" o "OCR_FORCED"
    """
    t_pdf = extract_text_pdfplumber(pdf_path)
    if is_text_sufficient(t_pdf):
        return t_pdf, "PDF_TEXT"

    # El texto extraído no es suficiente, forzar OCR
    t_ocr = extract_text_ocr(pdf_path, dpi=DPI)
    return t_ocr, "OCR_FORCED"


# ===================== FUNCIONES DE PROCESAMIENTO DE TEXTO =====================

def normalize_text(text: str) -> str:
    """
    Normaliza el texto para mejorar la extracción por el LLM.

    Operaciones:
    - Unifica saltos de línea (\\r\\n, \\r -> \\n)
    - Elimina espacios al final de líneas
    - Une palabras cortadas por guión al final de línea
    - Colapsa múltiples líneas vacías
    - Convierte saltos simples en espacios (preserva párrafos)
    - Normaliza espacios múltiples
    - Reemplaza caracteres especiales (ligaduras, guiones tipográficos)

    Args:
        text: Texto crudo extraído del PDF

    Returns:
        Texto normalizado y limpio
    """
    # Unificar saltos de línea
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Eliminar espacios al final de cada línea
    text = "\n".join(line.rstrip() for line in text.split("\n"))

    # Unir palabras cortadas: "pala-\nbra" -> "palabra"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    # Colapsar múltiples líneas vacías a máximo 2
    text = re.sub(r"\n{2,}", "\n\n", text)

    # Convertir saltos simples en espacios, preservando párrafos (doble salto)
    text = text.replace("\n\n", "__PARA__").replace("\n", " ").replace("__PARA__", "\n\n")

    # Normalizar espacios múltiples
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n\n *", "\n\n", text)

    # Reemplazar caracteres especiales por sus equivalentes ASCII
    replacements = {"ﬁ": "fi", "ﬂ": "fl", "—": "-", "–": "-", "•": "-"}
    for a, b in replacements.items():
        text = text.replace(a, b)

    return text.strip()


# ===================== FUNCIONES DE IA =====================

def llm_extract_items(text: str) -> dict:
    """
    Usa el LLM (GROQ/LLaMA) para extraer items estructurados del texto.

    Envía el texto normalizado al modelo con instrucciones específicas
    para extraer nombre, cantidad y precio de cada item.

    Args:
        text: Texto normalizado de la cotización

    Returns:
        Dict con estructura: {"items": [{"nombre": str, "cantidad": num, "precio": num}, ...]}

    Raises:
        RuntimeError: Si el modelo devuelve respuesta vacía
        json.JSONDecodeError: Si no se puede parsear la respuesta como JSON
    """
    prompt = f"""
Devuelve SOLO JSON: {{"items":[{{"nombre":string,"cantidad":number,"precio":number}}]}}

Reglas:
- No inventes. Numeros con punto decimal. Sin markdown.
- precio: usa el precio que aparece en el documento, NO dividas ni calcules.
- Nombre: incluye TODA la info del producto (tipo, material, dimensiones, specs). Solo quita acentos y Ø->O.
- Si el nombre tiene "20x", "100x" etc., mantenlo. NO agregues prefijos que no existan.
- MAXIMO 60 caracteres en nombre. Si supera, quita: "MATERIAL:", "DIMENSIONES:", "UBICACION:", "SERVICIO". Conserva solo: tipo + material + medidas.

Texto:
<<<
{text}
>>>
""".strip()

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "Eres un extractor. Responde SOLO JSON válido, sin markdown."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,  # Respuestas determinísticas
    )

    content = (resp.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("Respuesta vacía del modelo.")

    # Limpiar posibles bloques de código markdown que el modelo podría añadir
    content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\s*```$", "", content)

    # Intentar parsear JSON
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Fallback: buscar el primer objeto JSON válido en la respuesta
        m = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not m:
            print("Respuesta cruda del modelo:\n", content)
            raise
        return json.loads(m.group(0))


# ===================== ENDPOINTS DE LA API =====================

@app.route('/process', methods=['GET'])
def process_pdf():
    """
    Endpoint principal: procesa un PDF y extrae los items.

    Query params:
        pdf_url (str, requerido): URL directa al archivo PDF

    Returns:
        JSON con los items extraídos: {"items": [...]}

    Errores:
        400: Falta pdf_url o error descargando el PDF
        500: Error interno en el procesamiento
    """
    pdf_url = request.args.get('pdf_url')

    if not pdf_url:
        return jsonify({"error": "Falta el parámetro pdf_url"}), 400

    try:
        # 1. Descargar el PDF
        pdf_path = TMP_DIR / "input.pdf"
        download_pdf(pdf_url, pdf_path)

        # 2. Extraer texto (pdfplumber o OCR)
        raw_text, method = extract_text_smart(pdf_path)

        # 3. Normalizar texto
        norm_text = normalize_text(raw_text)

        print(f"\n{'='*50}")
        print(f"Método usado: {method}")
        print(f"Chars normalizado: {len(norm_text)}")
        print(f"{'='*50}")
        print("TEXTO ENVIADO AL LLM:")
        print(norm_text[:1000])  # Primeros 1000 chars
        print(f"{'='*50}")

        # 4. Extraer items con IA
        data = llm_extract_items(norm_text)

        return jsonify(data)

    except requests.RequestException as e:
        return jsonify({"error": f"Error descargando PDF: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/', methods=['GET'])
def index():
    """
    Endpoint raíz: información básica de la API.

    Returns:
        JSON con mensaje de bienvenida e instrucciones de uso
    """
    return jsonify({
        "message": "API de extracción de items de PDF",
        "uso": "/process?pdf_url=<URL_DEL_PDF>"
    })


# ===================== PUNTO DE ENTRADA =====================

if __name__ == "__main__":
    # Iniciar servidor Flask en modo desarrollo
    # host='0.0.0.0' permite conexiones externas
    app.run(debug=True, host='0.0.0.0', port=5000)
