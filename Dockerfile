# Hosted curriculum server. Host-agnostic: works on Fly.io, Render, or any
# platform that runs a container and can attach a persistent disk at /data.
#
# Build:   docker build -t curriculum-hosted .
# Run:     docker run --rm -p 8081:8080 -v curriculum-data:/data \
#            -e CURRICULUM_ALLOWED_HOSTS=<the public hostname> curriculum-hosted
#
# REQUIRED at run time: CURRICULUM_ALLOWED_HOSTS = the public hostname the
# platform serves (for example your-app.fly.dev). Without it the server answers
# every request with 421 "Invalid Host header". It is not baked in on purpose.
#
# Persistent data (all testers' databases and the token registry) lives in
# /data. Mount a platform volume there, or everything is lost on redeploy.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CURRICULUM_HOST=0.0.0.0 \
    CURRICULUM_PORT=8080 \
    CURRICULUM_DATA_DIR=/data \
    CURRICULUM_HOSTED_DIR=/data/hosted

# Unprivileged user that runs the server (see docker/entrypoint.sh).
RUN useradd --system --uid 10001 --user-group --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin curriculum \
    && mkdir -p /data/hosted \
    && chown -R 10001:10001 /data

WORKDIR /app

# Dependencies first, so editing the source doesn't re-download them.
COPY pyproject.toml ./
RUN python -c "import subprocess, sys, tomllib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; subprocess.check_call([sys.executable, '-m', 'pip', 'install', *deps])"

# The application itself (no tests, notes, exports, or databases: see .dockerignore).
COPY src ./src
RUN pip install --no-deps . \
    && rm -rf /app/src \
    && mkdir -p /usr/local/libexec \
    && mv /usr/local/bin/curriculum-admin /usr/local/libexec/curriculum-admin-real

COPY docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY docker/curriculum-admin /usr/local/bin/curriculum-admin
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh /usr/local/bin/curriculum-admin

VOLUME ["/data"]
EXPOSE 8080

# /healthz needs no token and touches no data.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('CURRICULUM_PORT', '8080'), timeout=3)"]

# Starts as root only to fix volume ownership, then drops to the app user.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["curriculum-hosted"]
