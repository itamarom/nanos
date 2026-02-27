# Stage 1: install dependencies
FROM python:3.12-slim AS deps
COPY worker/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Stage 2: mypy type-check (fails the build on errors)
FROM deps AS mypy-check
WORKDIR /app
COPY sdk/nanos_sdk/ /usr/local/lib/python3.12/site-packages/nanos_sdk/
COPY mypy.ini /app/mypy.ini
COPY shared/ /app/shared/
COPY worker/ /app/worker/
RUN pip install --no-cache-dir mypy \
    && python -m mypy worker/ shared/ \
    && touch /mypy-ok

# Stage 3: runtime
FROM deps

WORKDIR /app

COPY --from=mypy-check /mypy-ok /tmp/.mypy-ok

# SDK: always copy from the canonical source (sdk/), NOT from installed packages.
# This ensures the worker always has the latest generated client methods.
COPY sdk/nanos_sdk/ /usr/local/lib/python3.12/site-packages/nanos_sdk/

COPY shared/ /app/shared/
COPY worker/ /app/worker/

WORKDIR /app/worker

CMD ["python", "-m", "celery", "-A", "celery_app", "worker", "--loglevel=info"]
