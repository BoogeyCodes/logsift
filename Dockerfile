# The logsift web console.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[web]"

# Uploaded datasets persist here; mount a volume to keep them across restarts.
VOLUME ["/data"]
EXPOSE 8000

CMD ["logsift", "serve", "--db", "/data/logs.db", "--host", "0.0.0.0", "--port", "8000"]
