FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock pyproject.toml alembic.ini ./
COPY apps ./apps
COPY packages ./packages
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-deps --no-build-isolation . \
    && useradd --create-home studiosync
USER studiosync
EXPOSE 8765
CMD ["studiosync", "server", "start", "--host", "0.0.0.0"]
