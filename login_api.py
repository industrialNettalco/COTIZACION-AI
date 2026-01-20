# login_api.py - Módulo de autenticación para Claude AI
import requests
import json
import uuid
import logging

logger = logging.getLogger(__name__)

COOKIES_FILE = "claude_cookies_selenium.json"

def crear_session_login():
    """Crea una sesión con headers para login"""
    session = requests.Session()

    device_id = str(uuid.uuid4())
    anonymous_id = f"claudeai.v1.{uuid.uuid4()}"

    headers = {
        "accept": "*/*",
        "accept-language": "es-419,es;q=0.9",
        "anthropic-client-platform": "web_claude_ai",
        "anthropic-client-version": "1.0.0",
        "content-type": "application/json",
        "origin": "https://claude.ai",
        "referer": "https://claude.ai/login",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "anthropic-device-id": device_id,
        "anthropic-anonymous-id": anonymous_id,
    }

    session.headers.update(headers)
    return session

def enviar_codigo(email: str, session: requests.Session = None):
    """Envía código de verificación al email"""
    if session is None:
        session = crear_session_login()

    send_payload = {
        "email_address": email,
        "utc_offset": -300,
        "locale": "es-419",
        "login_intent": None,
        "oauth_client_id": None,
        "source": "claude"
    }

    response = session.post(
        "https://claude.ai/api/auth/send_magic_link",
        json=send_payload
    )

    if response.status_code == 429:
        data = response.json()
        return {
            "success": False,
            "error": f"Rate limit - {data['error']['message']}",
            "status_code": 429
        }
    elif response.status_code != 200:
        try:
            data = response.json()
            error_msg = data.get('error', {}).get('message', response.text)
        except:
            error_msg = response.text
        return {
            "success": False,
            "error": error_msg,
            "status_code": response.status_code
        }

    return {
        "success": True,
        "message": f"Código enviado a {email}",
        "session": session
    }

def verificar_codigo(email: str, code: str, session: requests.Session):
    """Verifica el código y guarda las cookies si es exitoso"""
    if len(code) != 6 or not code.isdigit():
        return {
            "success": False,
            "error": "El código debe ser de 6 dígitos"
        }

    verify_payload = {
        "credentials": {
            "method": "code",
            "email_address": email,
            "code": code
        },
        "locale": "es-419",
        "oauth_client_id": None,
        "source": "claude"
    }

    response = session.post(
        "https://claude.ai/api/auth/verify_magic_link",
        json=verify_payload
    )

    if response.status_code == 429:
        data = response.json()
        return {
            "success": False,
            "error": f"Rate limit - {data['error']['message']}",
            "status_code": 429
        }
    elif response.status_code == 401:
        return {
            "success": False,
            "error": "Código incorrecto o expirado",
            "status_code": 401
        }
    elif response.status_code != 200:
        try:
            data = response.json()
            error_msg = data.get('error', {}).get('message', response.text)
        except:
            error_msg = response.text
        return {
            "success": False,
            "error": error_msg,
            "status_code": response.status_code
        }

    data = response.json()

    if not data.get("success"):
        return {
            "success": False,
            "error": str(data)
        }

    # Extraer y guardar cookies
    cookies_to_save = []
    for cookie in session.cookies:
        cookie_dict = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
        }
        if cookie.expires:
            cookie_dict["expiry"] = cookie.expires
        cookies_to_save.append(cookie_dict)

    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies_to_save, f, indent=2)

    logger.info(f"✅ Login exitoso - {len(cookies_to_save)} cookies guardadas")

    return {
        "success": True,
        "message": "Login exitoso",
        "cookies_guardadas": len(cookies_to_save)
    }


# CLI para uso standalone
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    email = input("Email: ").strip()

    print(f"\nEnviando código a {email}...")
    resultado = enviar_codigo(email)

    if not resultado["success"]:
        print(f"Error: {resultado['error']}")
        exit(1)

    print("Código enviado! Revisa tu email.")
    session = resultado["session"]

    code = input("Código (6 dígitos): ").strip()

    print("\nVerificando código...")
    resultado = verificar_codigo(email, code, session)

    if resultado["success"]:
        print(f"✅ {resultado['message']}")
        print(f"   {resultado['cookies_guardadas']} cookies guardadas en '{COOKIES_FILE}'")
    else:
        print(f"❌ Error: {resultado['error']}")
