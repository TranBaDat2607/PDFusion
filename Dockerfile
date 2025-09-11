# Use Python 3.10 slim image as base
FROM python:3.10-slim

# Set environment variables
ENV DOCKER_ENV=true
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:0

# Install system dependencies for GUI applications
RUN apt-get update && apt-get install -y \
    gcc \
    libc-dev \
    libgl1 \
    libegl1 \
    libgles2 \
    libglib2.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrender1 \
    libxrandr2 \
    libxss1 \
    libxtst6 \
    libxcb1 \
    libxcb-glx0 \
    libxcb-keysyms1 \
    libxcb-image0 \
    libxcb-shm0 \
    libxcb-icccm4 \
    libxcb-sync1 \
    libxcb-xfixes0 \
    libxcb-shape0 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-util1 \
    libxkbcommon-x11-0 \
    libxkbcommon-x11-0 \
    xvfb \
    x11vnc \
    x11-utils \
    fluxbox \
    xauth \
    bash \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Make the entrypoint script executable
RUN chmod +x /app/docker-entrypoint.sh

# Create logs directory
RUN mkdir -p /app/logs

# Expose ports for VNC
EXPOSE 5900

# Define environment variable for logs
ENV LOGS_DIR=/app/logs

# Create a non-root user
RUN useradd --create-home --shell /bin/bash appuser
RUN chown -R appuser:appuser /app
USER appuser

# Use the bash entrypoint
ENTRYPOINT ["/app/docker-entrypoint.sh"]