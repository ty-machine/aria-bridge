# The interceptor as a container, for the homelab k3s cluster.
#
# It listens on 8080 as a non-root user. The cluster maps a LoadBalancer address on port 80
# to it, because the scale talks to port 80. All state lives in /data, which the cluster
# mounts from a persistent volume. Nothing is baked in, and nothing is sent anywhere.
FROM python:3.13-slim@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50

LABEL org.opencontainers.image.source="https://github.com/ty-machine/aria-bridge" \
      org.opencontainers.image.description="Fitbit Aria (FB201B) upload interceptor"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ARIA_PORT=8080 \
    ARIA_DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY aria_interceptor.py .
# One-off maintenance, run with `kubectl exec` while the service is stopped (see the runbook).
COPY scripts/dedup_data.py scripts/dedup_data.py

RUN groupadd --gid 10001 aria \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin aria \
 && mkdir /data \
 && chown 10001:10001 /data

USER 10001:10001
VOLUME /data
EXPOSE 8080

CMD ["python", "aria_interceptor.py"]
