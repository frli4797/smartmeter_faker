FROM python:3.12-slim

WORKDIR /app

ARG APP_VERSION=dev

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_VERSION=${APP_VERSION}
ENV HEALTH_STATUS_PATH=/tmp/smartmeter-faker-health.json
ENV HEALTHCHECK_MAX_AGE_SECONDS=30

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY modbus_bridge.py ./
COPY healthcheck.py ./
COPY homeassistant.yaml.example ./

EXPOSE 5020

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "healthcheck.py"]

CMD ["python", "modbus_bridge.py", "--config", "/app/homeassistant.yaml"]
