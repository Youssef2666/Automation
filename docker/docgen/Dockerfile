# docgen - small document toolbox for n8n workflows that need what n8n cannot do natively:
#   HTML -> PDF (WeasyPrint), DOCX/PPTX generation with Arabic RTL (python-docx / python-pptx),
#   PDF text + table extraction (pdfplumber), OCR in English and Arabic (Tesseract).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 \
        libffi8 libcairo2 libgdk-pixbuf-2.0-0 shared-mime-info \
        fontconfig fonts-dejavu-core \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-ara \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Amiri (SIL OFL) is bundled so Arabic renders identically everywhere - see fonts/OFL.txt
COPY fonts/ /app/fonts/
RUN mkdir -p /usr/local/share/fonts/amiri && cp /app/fonts/*.ttf /usr/local/share/fonts/amiri/ && fc-cache -f >/dev/null

COPY app.py ./

EXPOSE 8090
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
