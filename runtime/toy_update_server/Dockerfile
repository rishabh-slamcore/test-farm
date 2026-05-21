FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends iproute2 iputils-ping \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "test_farm.subjects.update_server_runtime"]
