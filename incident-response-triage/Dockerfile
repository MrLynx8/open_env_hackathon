FROM python:3.11-slim

WORKDIR /app

# Install dependencies first to take advantage of Docker layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code.
COPY . .

# Hugging Face Spaces expects apps to listen on 7860.
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/health')"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
