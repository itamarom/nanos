# Stage 1: install dependencies
FROM python:3.12-slim AS deps
COPY dashboard/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Stage 2: mypy type-check (fails the build on errors)
FROM deps AS mypy-check
WORKDIR /app
COPY mypy.ini /app/mypy.ini
COPY shared/ /app/shared/
COPY dashboard/ /app/dashboard/
RUN pip install --no-cache-dir mypy \
    && python -m mypy dashboard/ shared/ \
    && touch /mypy-ok

# Stage 3: runtime
FROM deps

WORKDIR /app

COPY --from=mypy-check /mypy-ok /tmp/.mypy-ok

COPY shared/ /app/shared/
COPY dashboard/ /app/dashboard/

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "dashboard.main:app", "--host", "0.0.0.0", "--port", "8001"]
