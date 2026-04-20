ARG BASE_PYTHON_IMAGE=python:3.12-slim
FROM ${BASE_PYTHON_IMAGE}

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    google-auth \
    google-auth-oauthlib \
    google-api-python-client

COPY gmail_bridge.py /app/gmail_bridge.py

EXPOSE 9090

CMD ["python", "/app/gmail_bridge.py", "--host", "0.0.0.0", "--port", "9090"]
