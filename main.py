from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict
import requests
import json
import time
import os
import tempfile
from contextlib import asynccontextmanager
import logging
from threading import Lock

import login_api

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

####################################################################################################################################################
# MODELOS

class DocumentoData(BaseModel):
    moneda: Optional[str] = None
    ruc: Optional[str] = None
    proveedor: Optional[str] = None
    codigo_factura: Optional[str] = None
    fecha_emision: Optional[str] = None
    forma_pago: Optional[str] = None
    igv: bool = False
    sub_total: Optional[str] = None
    total: Optional[str] = None

class ChatResponse(BaseModel):
    documento: DocumentoData
    tiempo_respuesta: float
    intentos: int = 1

class EmailRequest(BaseModel):
    email: str

class VerifyCodeRequest(BaseModel):
    email: str
    code: str

class LoginResponse(BaseModel):
    success: bool
    message: Optional[str] = None
    error: Optional[str] = None
    cookies_guardadas: Optional[int] = None

####################################################################################################################################################
# CONFIGURACION

RUC_NETTALCO = "20100064571"
MAX_INTENTOS = 5
TIMEOUT_RESPUESTA = 120

SYSTEM_PROMPT = """Extrae estos datos del PDF y responde SOLO con los valores separados por comas en este orden exacto:

Moneda,RUC,Proveedor,Codigo Factura,Fecha Emision,Forma Pago,IGV,Sub Total,Total

REGLAS:
- Moneda: SOLES o DOLARES
- RUC: solo numeros sin guiones del PROVEEDOR (NO de Nettalco/cliente que recibe). Si solo ves RUC 20100064571 pon null
- Proveedor: nombre completo empresa proveedora
- Codigo Factura: formato exacto
- Fecha Emision: formato DD/MM/YYYY
- Forma Pago: Contado o Credito
- IGV: True si incluye IGV o False si no incluye
- Sub Total: numero con punto decimal (monto sin IGV)
- Total: numero con punto decimal (si IGV es False entonces Total = Sub Total)
- Si no existe: null

EJEMPLO 1: SOLES,20190143806,MECANICA INDUSTRIAL LIRA S.A.C.,FACTURA 7 DIAS,10/01/2026,Credito,True,120.00,141.60
EJEMPLO 2: DOLARES,null,EMPRESA XYZ S.A.C.,F001-001,11/01/2026,Contado,False,500.00,500.00"""

####################################################################################################################################################
# CLASE CLAUDE SESSION

class ClaudeAPISession:
    def __init__(self):
        self.cookies_dict = self.cargar_cookies()
        self.request_lock = Lock()
        self.session = requests.Session()
        self.session.cookies.update(self.cookies_dict)

        self.headers = {
            "accept": "*/*",
            "accept-language": "es-419,es;q=0.9",
            "anthropic-client-platform": "web_claude_ai",
            "anthropic-device-id": self.cookies_dict.get("anthropic-device-id", ""),
            "anthropic-client-sha": "d989fcc79b6283027a05a06a0622b991cdb5f575",
            "anthropic-client-version": "1.0.0",
            "anthropic-anonymous-id": self.cookies_dict.get("ajs_anonymous_id", ""),
            "content-type": "application/json",
            "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
        }

        self.organization_id = self.obtener_organization_id()
        logger.info(f"Organization ID: {self.organization_id}")

    def obtener_organization_id(self):
        try:
            response = self.session.get(
                "https://claude.ai/api/organizations",
                headers=self.headers,
                timeout=10
            )

            if response.status_code == 200:
                orgs = response.json()
                if orgs and len(orgs) > 0:
                    org_id = orgs[0].get('uuid')
                    logger.info(f"Organization encontrada: {orgs[0].get('name', 'Sin nombre')}")
                    return org_id

            logger.error(f"Error obteniendo organizations: {response.status_code}")
            logger.error(f"Respuesta: {response.text}")
            return None

        except Exception as e:
            logger.error(f"Error obteniendo organization_id: {e}")
            return None

    def cargar_cookies(self):
        with open("claude_cookies_selenium.json", "r") as f:
            cookies_list = json.load(f)

        cookies_dict = {}
        for cookie in cookies_list:
            cookies_dict[cookie['name']] = cookie['value']

        logger.info(f"{len(cookies_dict)} cookies cargadas")
        return cookies_dict

    def subir_archivo(self, archivo_path):
        try:
            logger.info(f"Subiendo: {os.path.basename(archivo_path)}")

            url = f"https://claude.ai/api/{self.organization_id}/upload"

            with open(archivo_path, 'rb') as f:
                files = {
                    'file': (os.path.basename(archivo_path), f, 'application/pdf')
                }

                upload_headers = {k: v for k, v in self.headers.items() if k != 'content-type'}

                response = self.session.post(
                    url,
                    files=files,
                    headers=upload_headers,
                    timeout=30
                )

            if response.status_code == 200:
                data = response.json()
                file_uuid = data.get('file_uuid')
                logger.info(f"Archivo subido: {file_uuid}")
                return file_uuid
            else:
                logger.error(f"Error subiendo archivo: {response.status_code}")
                logger.error(f"Respuesta: {response.text}")
                logger.error(f"Headers respuesta: {dict(response.headers)}")
                return None

        except Exception as e:
            logger.error(f"Error: {e}")
            return None

    def crear_conversacion_y_enviar_mensaje(self, file_uuid, prompt):
        conversation_id = None

        try:
            logger.info("Creando conversacion...")
            create_url = f"https://claude.ai/api/organizations/{self.organization_id}/chat_conversations"

            create_body = {
                "uuid": None,
                "name": ""
            }

            response = self.session.post(
                create_url,
                json=create_body,
                headers=self.headers,
                timeout=10
            )

            if response.status_code != 201:
                logger.error(f"Error creando conversacion: {response.status_code}")
                logger.error(f"Respuesta: {response.text}")
                return None, None

            conv_data = response.json()
            conversation_id = conv_data.get('uuid')
            logger.info(f"Conversacion creada: {conversation_id}")

            time.sleep(1)

            logger.info("Enviando mensaje...")
            completion_url = f"https://claude.ai/api/organizations/{self.organization_id}/chat_conversations/{conversation_id}/completion"

            completion_body = {
                "prompt": prompt,
                "parent_message_uuid": "00000000-0000-4000-8000-000000000000",
                "timezone": "America/Lima",
                "personalized_styles": [{
                    "type": "default",
                    "key": "Default",
                    "name": "Normal",
                    "nameKey": "normal_style_name",
                    "prompt": "Normal\n",
                    "summary": "Default responses from Claude",
                    "summaryKey": "normal_style_summary",
                    "isDefault": True
                }],
                "locale": "es-419",
                "tools": [
                    {"type": "web_search_v0", "name": "web_search"},
                    {"type": "artifacts_v0", "name": "artifacts"},
                    {"type": "repl_v0", "name": "repl"}
                ],
                "attachments": [],
                "files": [file_uuid],
                "sync_sources": [],
                "rendering_mode": "messages"
            }

            stream_headers = self.headers.copy()
            stream_headers["accept"] = "text/event-stream, text/event-stream"
            stream_headers["Referer"] = f"https://claude.ai/chat/{conversation_id}"

            response = self.session.post(
                completion_url,
                json=completion_body,
                headers=stream_headers,
                stream=True,
                timeout=TIMEOUT_RESPUESTA
            )

            if response.status_code != 200:
                logger.error(f"Error enviando mensaje: {response.status_code}")
                logger.error(f"Respuesta: {response.text}")
                logger.error(f"Headers respuesta: {dict(response.headers)}")
                return conversation_id, None

            logger.info("Esperando respuesta...")
            texto_completo = ""

            for line in response.iter_lines():
                if line:
                    line = line.decode('utf-8')

                    if line.startswith('data:'):
                        data_json = line.split(':', 1)[1].strip()

                        try:
                            data = json.loads(data_json)

                            if data.get('type') == 'content_block_delta':
                                delta = data.get('delta', {})
                                if delta.get('type') == 'text_delta':
                                    texto_completo += delta.get('text', '')

                            elif data.get('type') == 'message_stop':
                                logger.info("Respuesta completa recibida")
                                break

                        except json.JSONDecodeError:
                            continue

            if texto_completo:
                texto_limpio = texto_completo.strip()
                logger.info(f"Respuesta: {len(texto_limpio)} caracteres")
                return conversation_id, texto_limpio
            else:
                logger.error("No se recibio respuesta")
                return conversation_id, None

        except requests.exceptions.Timeout:
            logger.error("Timeout esperando respuesta")
            return conversation_id, None
        except Exception as e:
            logger.error(f"Error: {e}")
            return conversation_id, None

    def eliminar_conversacion(self, conversation_id):
        if not conversation_id:
            return False

        try:
            url = f"https://claude.ai/api/organizations/{self.organization_id}/chat_conversations/{conversation_id}"

            response = self.session.delete(
                url,
                headers=self.headers,
                timeout=10
            )

            if response.status_code == 204:
                logger.info("Conversacion eliminada")
                return True
            else:
                logger.warning(f"Error eliminando: {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"Error: {e}")
            return False

    def parsear_respuesta_csv(self, raw_response):
        try:
            raw_response = raw_response.strip()
            partes = [p.strip() for p in raw_response.split(',')]

            if len(partes) < 9:
                logger.warning(f"Respuesta incompleta: {len(partes)} campos")
                partes.extend([None] * (9 - len(partes)))

            partes = [None if p and p.lower() == 'null' else p for p in partes]

            ruc = partes[1]
            if ruc == RUC_NETTALCO:
                logger.info(f"RUC detectado es de Nettalco, cambiando a null")
                ruc = None

            igv_value = False
            if partes[6]:
                igv_str = partes[6].lower()
                igv_value = igv_str in ['true', 'yes', 'si', 's', '1', 'verdadero']

            sub_total = partes[7]
            total = partes[8]

            if not igv_value and sub_total and not total:
                total = sub_total

            return DocumentoData(
                moneda=partes[0],
                ruc=ruc,
                proveedor=partes[2],
                codigo_factura=partes[3],
                fecha_emision=partes[4],
                forma_pago=partes[5],
                igv=igv_value,
                sub_total=sub_total,
                total=total
            )

        except Exception as e:
            logger.error(f"Error parseando: {e}")
            return DocumentoData()

    def intentar_procesamiento(self, archivo_path):
        conversation_id = None

        try:
            file_uuid = self.subir_archivo(archivo_path)
            if not file_uuid:
                raise Exception("Error subiendo archivo")

            time.sleep(1)

            conversation_id, respuesta_raw = self.crear_conversacion_y_enviar_mensaje(
                file_uuid,
                SYSTEM_PROMPT
            )

            if not respuesta_raw:
                raise Exception("Sin respuesta valida")

            documento = self.parsear_respuesta_csv(respuesta_raw)

            return conversation_id, documento

        except Exception as e:
            logger.error(f"Error en procesamiento: {e}")
            raise

    def procesar_consulta(self, archivo_path):
        with self.request_lock:
            inicio = time.time()
            ultimo_error = None

            for intento in range(1, MAX_INTENTOS + 1):
                conversation_id = None

                try:
                    logger.info(f"{'='*70}")
                    logger.info(f"INTENTO {intento}/{MAX_INTENTOS}")
                    logger.info(f"{'='*70}")

                    conversation_id, documento = self.intentar_procesamiento(archivo_path)
                    tiempo_total = time.time() - inicio

                    if conversation_id:
                        logger.info("Eliminando conversacion...")
                        self.eliminar_conversacion(conversation_id)

                    logger.info(f"EXITO en intento {intento}")
                    return documento, tiempo_total, intento

                except Exception as e:
                    ultimo_error = e
                    logger.error(f"Intento {intento} fallo: {e}")

                    if conversation_id:
                        try:
                            logger.info("Eliminando conversacion fallida...")
                            self.eliminar_conversacion(conversation_id)
                        except:
                            pass

                    if intento < MAX_INTENTOS:
                        espera = 3
                        logger.info(f"Esperando {espera}s antes del siguiente intento...")
                        time.sleep(espera)
                    else:
                        logger.error(f"FALLARON TODOS LOS INTENTOS ({MAX_INTENTOS})")
                        logger.error(f"Ultimo error: {ultimo_error}")
                        raise Exception(f"Fallo despues de {MAX_INTENTOS} intentos. Ultimo error: {ultimo_error}")

####################################################################################################################################################
# FASTAPI INIT

claude_session = None
login_sessions: Dict[str, requests.Session] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    global claude_session
    logger.info("Iniciando API...")
    try:
        claude_session = ClaudeAPISession()
    except FileNotFoundError:
        logger.warning("No se encontraron cookies. Usa /auth/send-code para autenticarte primero.")
        claude_session = None
    yield

app = FastAPI(
    title="Claude API Pure",
    version="4.0.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Chat", "description": "Endpoints para procesar PDFs con Claude"},
        {"name": "Auth", "description": "Endpoints de autenticacion y sesion"},
        {"name": "Health", "description": "Estado de la API"}
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

####################################################################################################################################################
# CHAT

@app.post("/chat/file", response_model=ChatResponse, tags=["Chat"])
async def chat_file_endpoint(file: UploadFile = File(...)):
    """Procesa un PDF subido directamente"""
    if claude_session is None:
        raise HTTPException(status_code=503, detail="No autenticado. Usa /auth/send-code primero.")

    try:
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Solo PDF")

        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        logger.info(f"Procesando: {file.filename}")

        try:
            documento, tiempo, intentos = claude_session.procesar_consulta(tmp_path)

            return ChatResponse(
                documento=documento,
                tiempo_respuesta=tiempo,
                intentos=intentos
            )
        finally:
            try:
                os.unlink(tmp_path)
            except:
                pass

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/chat/orden/{nombre_pdf}", response_model=ChatResponse, tags=["Chat"])
async def chat_orden_endpoint(nombre_pdf: str):
    """Procesa un PDF desde O:\\Publicar_Web\\Ordenes_Servicio por nombre"""
    if claude_session is None:
        raise HTTPException(status_code=503, detail="No autenticado. Usa /auth/send-code primero.")

    try:
        if not nombre_pdf.lower().endswith('.pdf'):
            nombre_pdf = f"{nombre_pdf}.pdf"

        ruta_base = r"O:\Publicar_Web\Ordenes_Servicio"
        archivo_path = os.path.join(ruta_base, nombre_pdf)

        if not os.path.exists(archivo_path):
            raise HTTPException(status_code=404, detail=f"Archivo no encontrado: {nombre_pdf}")

        logger.info(f"Procesando orden: {archivo_path}")

        documento, tiempo, intentos = claude_session.procesar_consulta(archivo_path)

        return ChatResponse(
            documento=documento,
            tiempo_respuesta=tiempo,
            intentos=intentos
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

####################################################################################################################################################
# AUTH

@app.post("/auth/send-code", response_model=LoginResponse, tags=["Auth"])
async def send_code_endpoint(request: EmailRequest):
    """Envia codigo de verificacion al email"""
    try:
        resultado = login_api.enviar_codigo(request.email)

        if resultado["success"]:
            login_sessions[request.email] = resultado["session"]
            return LoginResponse(
                success=True,
                message=resultado["message"]
            )
        else:
            return LoginResponse(
                success=False,
                error=resultado["error"]
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/auth/verify-code", response_model=LoginResponse, tags=["Auth"])
async def verify_code_endpoint(request: VerifyCodeRequest):
    """Verifica el codigo y guarda las cookies"""
    global claude_session

    try:
        session = login_sessions.get(request.email)
        if not session:
            return LoginResponse(
                success=False,
                error="Primero debes solicitar un codigo con /auth/send-code"
            )

        resultado = login_api.verificar_codigo(request.email, request.code, session)

        if resultado["success"]:
            del login_sessions[request.email]

            logger.info("Recargando sesion de Claude con nuevas cookies...")
            claude_session = ClaudeAPISession()

            return LoginResponse(
                success=True,
                message=resultado["message"],
                cookies_guardadas=resultado["cookies_guardadas"]
            )
        else:
            return LoginResponse(
                success=False,
                error=resultado["error"]
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/auth/reload-session", tags=["Auth"])
async def reload_session_endpoint():
    """Recarga la sesion de Claude desde las cookies guardadas"""
    global claude_session

    try:
        logger.info("Recargando sesion de Claude...")
        claude_session = ClaudeAPISession()

        return {
            "success": True,
            "message": "Sesion recargada",
            "organization_id": claude_session.organization_id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

####################################################################################################################################################
# HEALTH

@app.get("/health", tags=["Health"])
async def health():
    """Verifica el estado de la API"""
    return {"ok": True, "api_version": "4.0.0"}

####################################################################################################################################################

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
