# COTIZACION-AI

API REST para extraccion de items desde PDFs de cotizaciones usando IA.

## Requisitos

- Python 3.10.19
- Poppler (para conversion PDF a imagen)
- Tesseract OCR (para PDFs escaneados)
- GROQ API Key

## Instalacion

1. Clonar el repositorio:
```bash
git clone <url-del-repo>
cd COTIZACION-AI
```

2. Crear entorno virtual:
```bash
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac
```

3. Instalar dependencias:
```bash
pip install -r requirements.txt
```

4. Instalar Poppler (Windows con Conda):
```bash
conda install -c conda-forge poppler
```

5. Instalar Tesseract:
- Windows: Descargar desde https://github.com/UB-Mannheim/tesseract/wiki
- Linux: `sudo apt install tesseract-ocr tesseract-ocr-spa`

6. Configurar variables de entorno:
```bash
# Crear archivo .env
GROQ_API_KEY=tu_api_key_aqui
```

## Uso

1. Iniciar el servidor:
```bash
python app.py
```

2. El servidor estara disponible en `http://localhost:5000`

## Endpoints

### GET /
Informacion de la API.

**Respuesta:**
```json
{
  "message": "API de extraccion de items de PDF",
  "uso": "/process?pdf_url=<URL_DEL_PDF>"
}
```

### GET /process
Procesa un PDF y extrae los items.

**Parametros:**
- `pdf_url` (requerido): URL directa al archivo PDF

**Ejemplo:**
```
GET /process?pdf_url=https://ejemplo.com/cotizacion.pdf
```

**Respuesta exitosa:**
```json
{
  "items": [
    {
      "nombre": "PERNO SOCKET CILINDRICO M3X0.5X8MM ACERO GR 12.9",
      "cantidad": 100,
      "precio": 1.0
    },
    {
      "nombre": "ARANDELA PLANA ACERO TEMPLADO 0.6XO46XO35MM",
      "cantidad": 12,
      "precio": 20.0
    }
  ]
}
```

**Errores:**
- `400`: Falta pdf_url o error descargando el PDF
- `500`: Error interno en el procesamiento

## Caracteristicas

- Extraccion de texto con pdfplumber (rapido)
- OCR con Tesseract para PDFs escaneados (fallback automatico)
- Modelo LLaMA 3.3 70B via GROQ API
- Normalizacion de texto (acentos, simbolos especiales)
- Nombres de productos limitados a 60 caracteres

## Estructura del Proyecto

```
COTIZACION-AI/
├── app.py              # Aplicacion principal
├── requirements.txt    # Dependencias Python
├── .env               # Variables de entorno (no incluido)
├── tmp/               # PDFs temporales descargados
└── README.md          # Este archivo
```

## Licencia

MIT
