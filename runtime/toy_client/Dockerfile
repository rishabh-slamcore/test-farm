FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "test_farm.subjects.toy_client_runtime"]
