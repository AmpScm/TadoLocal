FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*

COPY . /app
RUN pip install --no-cache-dir /app \
    && chmod +x /app/entrypoint.sh

RUN useradd -u 1000 -m tado
USER tado

VOLUME /data
EXPOSE 4407

ENTRYPOINT ["/app/entrypoint.sh"]
