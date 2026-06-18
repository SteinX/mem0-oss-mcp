FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

ENV PYTHONPATH=/app/src
EXPOSE 8080

CMD ["python", "-m", "mem0_oss_mcp.server"]
