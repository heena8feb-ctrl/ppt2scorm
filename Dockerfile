# ppt2scorm web app
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860
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
