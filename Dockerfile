FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies including ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Download standalone Tailwind CLI and compile CSS
RUN curl -sLO https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-linux-x64 && \
    chmod +x tailwindcss-linux-x64

# Copy project files
COPY . /app/

# Build Tailwind CSS
RUN ./tailwindcss-linux-x64 -i dashboard/static/dashboard/input.css -o dashboard/static/dashboard/output.css --minify && \
    rm tailwindcss-linux-x64

# Collect static files
RUN DJANGO_SECRET_KEY=build-placeholder RUN_SCHEDULER=0 python manage.py collectstatic --noinput

RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

CMD ["/app/entrypoint.sh"]
