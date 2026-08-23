# Build an immutable, hermetic runtime container for the GitHub Action.
# Why Docker?
# 1. Native Python isolation: Unlike Node.js, GitHub Actions runners don't natively execute Python without setup overhead.
# 2. Dependency Hermeticity: Bundles system utilities (ripgrep, git) and all Python dependencies in an immutable image.
# 3. Predictable performance: Zero runtime dependency installation overhead during CI workflow execution.

FROM python:3.11-slim

# Install system dependencies: git and ripgrep for local codebase search
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ripgrep \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# Copy dependency specifications first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY src/ /app/src/

ENTRYPOINT ["python", "/app/src/entrypoint.py"]
