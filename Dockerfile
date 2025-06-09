FROM python:3.11-slim

# Set environment to non-interactive
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies and add Microsoft repository
RUN apt-get update && \
    apt-get install -y curl gnupg2 ca-certificates apt-transport-https && \
    curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /etc/apt/trusted.gpg.d/microsoft.gpg && \
    echo "deb [arch=amd64 signed-by=/etc/apt/trusted.gpg.d/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql17 unixodbc unixodbc-dev gcc g++ libffi-dev libssl-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy application files
COPY . /app

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the default Flask port
EXPOSE 5000

# Run your application with Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
