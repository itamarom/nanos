FROM python:3.12-slim

COPY sdk/nanos_sdk/ /usr/local/lib/python3.12/site-packages/nanos_sdk/
COPY nano-runner/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
