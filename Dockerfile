FROM python:3.11-slim

# Python environment variables
ENV PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100

# Poetry environment variables
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=true \
    POETRY_CACHE_DIR='/var/cache/pypoetry' \
    POETRY_HOME='/usr/local' \
    POETRY_VERSION=1.8.3

# Set your custom environment variable
ENV YOUR_ENV=auctioner_env

# Install curl and poetry
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN curl -sSL https://install.python-poetry.org | python3 -

# Set the working directory
WORKDIR /app

# Copy poetry configuration files
COPY pyproject.toml poetry.lock /app/

# Install dependencies (poetry will create a virtual environment)
RUN poetry install --no-root

# Copy the rest of the application
COPY . /app
COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh

CMD ["./app/run.sh"]