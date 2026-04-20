ARG BASE_NODE_IMAGE=node:20-bookworm-slim
ARG BASE_PYTHON_IMAGE=python:3.12-slim

FROM ${BASE_NODE_IMAGE} AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM ${BASE_PYTHON_IMAGE} AS runtime

ARG CAMOUFOX_VERSION=135.0.1
ARG CAMOUFOX_RELEASE=beta.24
ARG DEBIAN_MIRROR=deb.debian.org

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    APP_CONDA_ENV=docker \
    APP_RELOAD=0 \
    APP_RUNTIME_DIR=/runtime \
    APP_ENABLE_SOLVER=1 \
    SOLVER_PORT=8889 \
    SOLVER_BIND_HOST=0.0.0.0 \
    LOCAL_SOLVER_URL=http://127.0.0.1:8889 \
    SOLVER_BROWSER_TYPE=camoufox

WORKDIR /app

COPY requirements.txt ./
COPY scripts/install_camoufox.py /tmp/install_camoufox.py

RUN set -eux; \
    sed -i "s|deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    apt-get -o Acquire::Retries=5 -o Acquire::ForceIPv4=true update; \
    apt-get -o Acquire::Retries=5 -o Acquire::ForceIPv4=true install -y --no-install-recommends \
        curl ca-certificates \
        libgtk-3-0 libx11-xcb1 libasound2 xvfb xauth; \
    for attempt in 1 2 3; do \
      curl -fsSL https://go.dev/dl/go1.24.2.linux-amd64.tar.gz | tar -C /usr/local -xz && break; \
      if [ "$attempt" -eq 3 ]; then exit 1; fi; \
      echo "go download failed, retrying ($attempt/3)..." >&2; \
      sleep 3; \
    done; \
    for attempt in 1 2 3; do \
      curl -LsSf https://astral.sh/uv/install.sh | sh && break; \
      if [ "$attempt" -eq 3 ]; then exit 1; fi; \
      echo "uv install script failed, retrying ($attempt/3)..." >&2; \
      sleep 3; \
    done; \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/usr/local/go/bin:/root/.local/bin:${PATH}"

RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && installed=0 \
    && for attempt in 1 2 3; do \
         if python -m playwright install --with-deps chromium firefox; then \
           installed=1; \
           break; \
         fi; \
         if [ "$attempt" -eq 3 ]; then break; fi; \
         echo "playwright browser install failed, retrying ($attempt/3)..." >&2; \
         sleep 5; \
       done \
    && [ "$installed" -eq 1 ] \
    && CAMOUFOX_VERSION="$CAMOUFOX_VERSION" CAMOUFOX_RELEASE="$CAMOUFOX_RELEASE" python /tmp/install_camoufox.py

COPY . .
COPY --from=frontend-builder /app/static /app/static

RUN apt-get -o Acquire::Retries=5 -o Acquire::ForceIPv4=true update && apt-get -o Acquire::Retries=5 -o Acquire::ForceIPv4=true install -y --no-install-recommends dos2unix git iproute2 procps \
    && dos2unix /app/docker/entrypoint.sh \
    && chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /runtime /runtime/logs /runtime/smstome_used /_ext_targets \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 8000 8889

VOLUME ["/runtime", "/_ext_targets"]

ENTRYPOINT ["/app/docker/entrypoint.sh"]
