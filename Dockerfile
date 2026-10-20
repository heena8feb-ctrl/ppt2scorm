# ppt2scorm web app
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860

# LibreOffice renders slides with charts, SmartArt and shape diagrams into images; poppler turns the PDF into PNGs.
# Build with --build-arg WITH_RENDERING=0 for a much smaller image without that feature.
ARG WITH_RENDERING=1
RUN if [ "$WITH_RENDERING" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libreoffice-impress poppler-utils fonts-dejavu fonts-liberation \
      && rm -rf /var/lib/apt/lists/*; \
    fi
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ppt2scorm ./ppt2scorm
COPY player ./player
COPY web ./web

# Run as a non-root user
RUN useradd --create-home appuser
USER appuser

EXPOSE 7860
CMD uvicorn web.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*"
