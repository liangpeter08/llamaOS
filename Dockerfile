# One image for the Python services; docker-compose picks the command per service.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common ./common
COPY core ./core
COPY dsl ./dsl
COPY backtest ./backtest
COPY services/__init__.py ./services/__init__.py
COPY services/broker ./services/broker
COPY services/models ./services/models
COPY services/api ./services/api
COPY prompts ./prompts
COPY scripts ./scripts
RUN useradd --create-home --uid 10001 app
USER app
