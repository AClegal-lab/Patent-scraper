FROM python:3.13-slim

WORKDIR /app

# Install system dependencies for pymupdf
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Create data directories
RUN mkdir -p data logs

EXPOSE 8080

# Default: production mode with gunicorn
CMD ["gunicorn", "wsgi:app", "-b", "0.0.0.0:8080", "-w", "2", "--timeout", "120"]
