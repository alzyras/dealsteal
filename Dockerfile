FROM python:3.11-slim

# Python environment variables
ENV PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100

# Set your custom environment variable
ENV YOUR_ENV=auctioner_env

# Install curl and git (required for some dependencies)
RUN apt-get update && apt-get install -y curl git && rm -rf /var/lib/apt/lists/*

# Set the working directory to /app for the project files
WORKDIR /app

# Copy the requirements.txt file and install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application (including 'src' directory and store/item_queries)
COPY . /app/

# Set the PYTHONPATH environment variable to include 'src' folder for Python imports
ENV PYTHONPATH=/app/src:$PYTHONPATH

# Set the working directory to where `runner.py` is located (for imports)
WORKDIR /app/src/dealsteal

# Ensure `runner.py` runs with the correct working directory to access JSON files
CMD ["sh", "-c", "cd /app && python /app/src/dealsteal/runner.py"]