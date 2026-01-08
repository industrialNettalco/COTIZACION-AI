# cotizacion-ai

## 📄 Descripción
**cotizacion-ai** es una API desarrollada con **FastAPI** que permite procesar archivos PDF de cotizaciones, convertirlos a imágenes y utilizar modelos **Groq Vision (Llama 4)** para extraer información estructurada del documento.

La API retorna un **JSON estandarizado** con:
- Datos del encabezado del documento (empresa, RUC, factura, moneda, IGV, etc.).
- Detalle de ítems (nombre, cantidad, precio unitario, unidad y adicionales).

El proyecto está alineado con los **Estándares de Desarrollo del Área de Planeamiento**.

---

## 🎯 Objetivos
- Automatizar la lectura de cotizaciones en PDF.
- Reducir errores manuales en el registro de información.
- Estandarizar la salida de datos para integraciones internas.
- Facilitar la mantenibilidad y escalabilidad del servicio.

---

## 🧱 Tecnologías utilizadas
- Python 3.10+
- FastAPI
- Uvicorn
- Groq API (Vision – Llama 4)
- pdf2image + Poppler
- Loguru
- python-dotenv

---

## 📁 Estructura del proyecto
```
cotizacion-ai/
├── app_fastapi.py
├── requirements.txt
├── README.md
├── .env.example
├── tmp/
└── serverAPI.log
```

---

## ⚙️ Configuración del entorno

### Crear entorno virtual
```
python -m venv venv
venv\Scripts\activate
```

### Instalar dependencias
```
pip install -r requirements.txt
```

### Variables de entorno
Crear archivo `.env` (no versionado):

```
GROQ_API_KEY=your_groq_api_key_here
GROQ_VISION_MODEL=meta-llama/llama-4-maverick-17b-128e-instruct
PDF_IMG_DPI=220
PDF_MAX_PAGES=10
PDF_BASE_DIR=O:\Publicar_Web\Ordenes_Servicio
```

---

## ▶️ Ejecución
```
uvicorn app_fastapi:app --host 0.0.0.0 --port 5000 --reload
```

---

## 🔌 Endpoints

### Home
GET /home

### Información
GET /

### Procesar PDF
GET /process?pdf=archivo.pdf

---

## 🪵 Logging
- Archivo: serverAPI.log
- Nivel: INFO
- Rotación automática

---

## 🔐 Seguridad
- Variables sensibles en `.env`
- `.env` ignorado por Git
- Accesos controlados por responsables del área

---

## 🚀 Pase a Producción
- Prueba flujo principal (2 veces)
- Prueba flujos secundarios
- Revisión de código
- Validación con PO y Jefaturas
