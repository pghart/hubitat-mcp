FROM python:3.12-slim

# Run as non-root
RUN useradd --create-home --shell /bin/bash --uid 1000 mcp

WORKDIR /app

# Deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# Audit log directory (will also be a mounted volume)
RUN mkdir -p /var/log/hubitat-mcp && chown -R mcp:mcp /var/log/hubitat-mcp /app

USER mcp

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
    sys.exit(0 if urllib.request.urlopen('http://localhost:8765/health', timeout=3).status==200 else 1)"

CMD ["python", "server.py"]
