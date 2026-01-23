FROM python:3.11-slim

# Install system dependencies required for WeasyPrint and other tools
RUN apt-get update && apt-get install -y \
    build-essential \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create directory for persistent data (User data, ChromaDB)
RUN mkdir -p /app/users /app/chroma_db

# Expose port
EXPOSE 5001

# Environment variables (Defaults, can be overridden)
ENV PORT=5001
ENV FLASK_APP=app.py

# Command to run the application using Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "2", "--timeout", "120", "app:app"]
