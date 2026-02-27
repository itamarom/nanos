# Stage 1: Build wacli from source
FROM golang:1.25-bookworm AS wacli-builder
RUN apt-get update && apt-get install -y git
RUN git clone https://github.com/steipete/wacli.git /src/wacli \
    && cd /src/wacli \
    && go build -tags sqlite_fts5 -o /usr/local/bin/wacli ./cmd/wacli

# Stage 2: install dependencies
FROM python:3.12-slim AS deps
COPY gateway/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Stage 3: mypy type-check (fails the build on errors)
FROM deps AS mypy-check
WORKDIR /app
COPY mypy.ini /app/mypy.ini
COPY shared/ /app/shared/
COPY gateway/ /app/gateway/
COPY sdk/ /app/sdk/
RUN pip install --no-cache-dir mypy \
    && python -m mypy gateway/ shared/ sdk/ \
    && touch /mypy-ok

# Stage 4: Python gateway
FROM deps

WORKDIR /app

COPY --from=mypy-check /mypy-ok /tmp/.mypy-ok
COPY --from=wacli-builder /usr/local/bin/wacli /usr/local/bin/wacli

COPY shared/ /app/shared/
COPY gateway/ /app/gateway/
COPY alembic/ /app/alembic/
COPY sdk/nanos_sdk/ /app/sdk/nanos_sdk/

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
