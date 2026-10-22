# ppt2scorm web app
# Base image pinned to the Python 3.12 line on Debian bookworm (Dependabot proposes updates).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860

# LibreOffice renders slides with charts, SmartArt and shape diagrams into images; poppler turns the PDF into PNGs.
# Build with --build-arg WITH_RENDERING=0 for a much smaller image without that feature.
ARG WITH_RENDERING=1
RUN if [ "$WITH_RENDERING" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libreoffice-impress poppler-utils fonts-dejavu fonts-liberation \
      && rm -rf /var/lib/apt/lists/*; \
    fi
WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY ppt2scorm ./ppt2scorm
COPY player ./player
COPY web ./web

# Run as a non-root user
RUN useradd --create-home appuser
USER appuser

EXPOSE 7860
# No --proxy-headers: the visitor's IP is read in web/security.py from a header the host sets and visitors can't
# forge (PPT2SCORM_CLIENT_IP_HEADER) or from a fixed number of proxy hops (PPT2SCORM_PROXY_HOPS).
CMD uvicorn web.app:app --host 0.0.0.0 --port ${PORT} --no-server-header
