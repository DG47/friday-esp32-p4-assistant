FROM docker.io/library/python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

ARG REQUIREMENTS_LOCK=requirements.linux-arm64.lock
COPY requirements.txt requirements.linux-arm64.lock ./
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes --index-url https://pypi.org/simple -r ${REQUIREMENTS_LOCK}

COPY app ./app
COPY tests ./tests
RUN useradd --uid 10001 --create-home friday && mkdir -p /app/data /models \
    && chown -R friday:friday /app/data /models

USER friday

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-ping-timeout", "60", "--ws-max-size", "1048576", "--no-proxy-headers"]
