# Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY main.py login_api.py ./
# Copiar cookies si existen (opcional)
COPY claude_cookies_selenium.jso[n] ./

EXPOSE 8001

# Agregar label para identificación
LABEL version="claude_v3"
LABEL description="Claude API v3 - Pure API with email authentication"

CMD ["python3", "main.py"]