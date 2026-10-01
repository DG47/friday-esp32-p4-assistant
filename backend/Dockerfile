FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tests ./tests
RUN useradd --uid 10001 --create-home friday && mkdir -p /app/data /models \
    && chown -R friday:friday /app/data /models

USER friday

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-ping-timeout", "60", "--ws-max-size", "1048576", "--no-proxy-headers"]
