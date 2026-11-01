FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY services/bot/pyproject.toml ./
RUN python -c "import tomllib;print(' '.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" \
    | xargs pip install

COPY services/bot/ ./
COPY shared/ /app/shared/

CMD ["python", "-m", "app.main"]
