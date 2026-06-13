FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# LibreOffice Calc (headless) — для пересчёта формул (скилл, раздел 9). Без рекомендаций,
# чтобы не тянуть лишнее; UNO/python-uno не нужны — используем CLI soffice --convert-to.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-calc libreoffice-core \
    && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp

# Light deps: pandas/openpyxl/langchain-openai/deepagents (no torch)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir "fastapi>=0.115" "uvicorn>=0.30" "python-multipart>=0.0.9"

COPY . .

# CLI agent: pass args after the image name, e.g.
#   docker run --rm -e OPENROUTER_API_KEY=... excel-agent --fresh --workdir runs/demo "task"
ENTRYPOINT ["python", "main.py"]
