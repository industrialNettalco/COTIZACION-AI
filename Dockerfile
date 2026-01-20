# Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY main.py login_api.py claude_cookies_selenium.json ./

EXPOSE 8001

# Agregar label para identificación
LABEL version="claude_v2"
LABEL description="Claude API v2 - Pure API without Selenium"

CMD ["python3", "main.py"]