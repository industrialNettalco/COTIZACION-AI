import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import json

# Configurar navegador anti-detección
options = uc.ChromeOptions()
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

driver = uc.Chrome(options=options, version_main=None)

print("🌐 Abriendo Claude.ai con modo stealth...")
driver.get("https://claude.ai")

print("\n⏳ Esperando carga inicial...")
time.sleep(10)

print("\n📋 Instrucciones:")
print("1. Si ves verificación de Cloudflare, espera a que pase")
print("2. Haz clic en 'Login' o 'Sign In'")
print("3. Inicia sesión con tu cuenta (Google/Email)")
print("4. Cuando veas la interfaz de chat de Claude, vuelve aquí\n")

input("⏸️  Presiona Enter cuando hayas iniciado sesión...")

# Verificar login
try:
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "[contenteditable='true'], textarea, .ProseMirror"))
    )
    print("✅ ¡Login exitoso! Interfaz de Claude detectada")
except:
    print("⚠️  No se detectó la interfaz de chat, pero guardando cookies de todas formas...")

# Guardar cookies
cookies = driver.get_cookies()
with open("claude_cookies_selenium.json", "w", encoding="utf-8") as f:
    json.dump(cookies, f, indent=2)

print(f"✅ {len(cookies)} cookies guardadas en 'claude_cookies_selenium.json'")

# Mostrar cookies importantes
print("\n🔑 Cookies importantes detectadas:")
for cookie in cookies:
    if cookie['name'] in ['__cf_bm', '__ssid', 'sessionKey', 'activitySessionId']:
        print(f"  - {cookie['name']}: {cookie['value'][:30]}...")

input("\n✅ Presiona Enter para cerrar el navegador...")
driver.quit()