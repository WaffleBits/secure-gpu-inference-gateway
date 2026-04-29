FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV AUDIT_LOG_PATH=/var/log/gateway/audit.log

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN adduser --system --uid 10001 --group gateway \
    && mkdir -p /var/log/gateway \
    && chown -R gateway:gateway /var/log/gateway

COPY --chown=gateway:gateway gateway ./gateway

USER gateway

EXPOSE 8000

CMD ["uvicorn", "gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
