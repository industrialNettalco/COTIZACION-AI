# COTIZACION-AI

API para procesar PDFs de facturas/cotizaciones usando Claude AI.

## Requisitos

- Python 3.10+
- Archivo `claude_cookies_selenium.json` con cookies válidas de Claude

## Instalación

```bash
pip install -r requirements.txt
```

## Iniciar el servidor

```bash
python main.py
```

El servidor estará disponible en `http://localhost:8001`

---

## Pasos para usar la API

### 1. Autenticación (Primera vez o cookies expiradas)

#### Paso 1: Solicitar código de verificación

```bash
POST /auth/send-code
Content-Type: application/json

{
  "email": "tu_email@ejemplo.com"
}
```

#### Paso 2: Verificar código recibido por email

```bash
POST /auth/verify-code
Content-Type: application/json

{
  "email": "tu_email@ejemplo.com",
  "code": "123456"
}
```

Esto guarda las cookies en `claude_cookies_selenium.json` y recarga la sesión automáticamente.

### 2. Procesar PDFs

#### Opción A: Subir archivo directamente

```bash
POST /chat/file
Content-Type: multipart/form-data

file: [archivo.pdf]
```

#### Opción B: Procesar desde ruta de red (Ordenes)

```bash
POST /chat/orden/{nombre_pdf}
```

Busca el PDF en `O:\Publicar_Web\Ordenes_Servicio\`

---

## Endpoints

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| POST | `/auth/send-code` | Envía código de verificación al email |
| POST | `/auth/verify-code` | Verifica código y guarda cookies |
| POST | `/auth/reload-session` | Recarga sesión desde cookies guardadas |
| POST | `/chat/file` | Procesa PDF subido |
| POST | `/chat/orden/{nombre_pdf}` | Procesa PDF desde carpeta de órdenes |
| GET | `/health` | Estado de la API |

---

## Respuesta de procesamiento

```json
{
  "documento": {
    "moneda": "SOLES",
    "ruc": "20190143806",
    "proveedor": "EMPRESA S.A.C.",
    "codigo_factura": "F001-001234",
    "fecha_emision": "15/01/2026",
    "forma_pago": "Credito",
    "igv": true,
    "sub_total": "100.00",
    "total": "118.00"
  },
  "tiempo_respuesta": 5.23,
  "intentos": 1
}
```

---

## Documentación interactiva

- Swagger UI: `http://localhost:8001/docs`
- ReDoc: `http://localhost:8001/redoc`
