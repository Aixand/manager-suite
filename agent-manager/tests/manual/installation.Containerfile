FROM docker.io/library/python:3.13-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       bash ca-certificates curl git gzip libatomic1 libstdc++6 tar unzip xz-utils \
    && rm -rf /var/lib/apt/lists/*

COPY aixand-lib-local-runtime /src/aixand-lib-local-runtime
COPY aixand-lib-filesystem /src/aixand-lib-filesystem
COPY agent-manager /src/agent-manager
RUN python3 -m venv /opt/agent \
    && /opt/agent/bin/python -m pip install --disable-pip-version-check --no-cache-dir \
       /src/aixand-lib-filesystem /src/aixand-lib-local-runtime /src/agent-manager
COPY installation_smoke.py /opt/installation_smoke.py

RUN useradd --create-home --uid 10001 agent-smoke
ENV HOME=/home/agent-smoke \
    XDG_CONFIG_HOME=/home/agent-smoke/.config \
    XDG_DATA_HOME=/home/agent-smoke/.local/share \
    XDG_STATE_HOME=/home/agent-smoke/.local/state \
    XDG_CACHE_HOME=/home/agent-smoke/.cache \
    AGENT_MANAGER_HOME=/home/agent-smoke/.aixand/agent-manager/state \
    AGENT_MANAGER_CONFIG=/home/agent-smoke/.aixand/agent-manager/providers.toml \
    AGENT_INSTALL_SMOKE_CONTAINER=1 \
    PATH=/opt/agent/bin:/home/agent-smoke/.local/bin:/home/agent-smoke/.opencode/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONUNBUFFERED=1

USER agent-smoke
WORKDIR /home/agent-smoke
ENTRYPOINT ["/opt/agent/bin/python", "/opt/installation_smoke.py"]
