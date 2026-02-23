FROM python:3.11-slim

# System dependencies for WeasyPrint PDF generation and PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    libpq-dev \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory (overlaid by persistent volume in production)
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["gunicorn", "local_app:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
