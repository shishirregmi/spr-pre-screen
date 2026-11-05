FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SPR_DATA_DIR=/data

WORKDIR /app

# antiword/catdoc read legacy Word 97-2003 (.doc) files; tesseract reads text inside pictures (screenshots, scans)
RUN apt-get update \
    && (apt-get install -y --no-install-recommends antiword || apt-get install -y --no-install-recommends catdoc) \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s "$(dirname "$(find /usr/share/tesseract-ocr -name eng.traineddata | head -1)")" /usr/share/tessdata
ENV TESSDATA_PREFIX=/usr/share/tessdata

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY criteria ./criteria
COPY web ./web

RUN useradd --create-home spr && mkdir -p /data && chown -R spr /data /app/criteria
USER spr

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
