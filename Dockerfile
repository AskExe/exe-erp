# ──────────────────────────────────────────────────────────────
# Exe ERP — Production Dockerfile (multi-stage)
# Based on frappe/frappe_docker Containerfile pattern
# Postgres-only, no MariaDB
# ──────────────────────────────────────────────────────────────

# ── Stage 1: Base runtime ────────────────────────────────────
# Pin the base image by immutable digest (not the mutable 3.14-slim-bookworm
# tag) so builds are reproducible and cannot be silently re-pointed at a
# different image. Multi-arch index digest covers linux/amd64 + linux/arm64.
# To update: docker manifest inspect python:3.14-slim-bookworm, take the
# docker-content-digest, and bump the comment tag below.
# Tag at pin time: python:3.14-slim-bookworm (resolved 2026-06-29)
FROM python:3.14-slim-bookworm@sha256:4ff4b92a68355dbdb52584ab3391dff8d371a61d4e063468bfd0130e3189c6d9 AS base

# System deps for runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    # PostgreSQL client
    libpq5 \
    postgresql-client \
    # wkhtmltopdf for PDF generation
    wkhtmltopdf \
    xvfb \
    # Nginx
    nginx \
    # Redis CLI (for health checks in entrypoint)
    redis-tools \
    # Misc runtime deps
    curl \
    wget \
    git \
    # xz-utils — needed to unpack the official Node.js .tar.xz below
    xz-utils \
    # Locale
    locales \
    && sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen \
    && locale-gen en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8

# Install Node.js 24 from the official nodejs.org binary distribution with a
# SHA256 checksum check.
#
# We deliberately do NOT use the NodeSource `curl | bash` setup script: piping
# a remote installer straight into a root shell with no integrity check means a
# compromised/MITM'd script executes arbitrary code at build time. Instead we
# download the pinned, prebuilt tarball and verify it against the published
# SHA256 from nodejs.org's signed SHASUMS256.txt before unpacking.
#
# To update: bump NODE_VERSION and refresh both checksums from
#   https://nodejs.org/dist/${NODE_VERSION}/SHASUMS256.txt
ARG NODE_VERSION=v24.18.0
ARG NODE_SHA256_AMD64=55aa7153f9d88f28d765fcdad5ae6945b5c0f98a36881703817e4c450fa76742
ARG NODE_SHA256_ARM64=58c9520501f6ae2b52d5b210444e24b9d0c029a58c5011b797bc1fe7105886f6
RUN set -eux; \
    case "$(dpkg --print-architecture)" in \
        amd64) node_arch=x64; node_sha="${NODE_SHA256_AMD64}" ;; \
        arm64) node_arch=arm64; node_sha="${NODE_SHA256_ARM64}" ;; \
        *) echo "Unsupported architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    tarball="node-${NODE_VERSION}-linux-${node_arch}.tar.xz"; \
    curl -fsSLo "/tmp/${tarball}" "https://nodejs.org/dist/${NODE_VERSION}/${tarball}"; \
    echo "${node_sha}  /tmp/${tarball}" | sha256sum -c -; \
    tar -xJf "/tmp/${tarball}" -C /usr/local --strip-components=1 --no-same-owner; \
    rm -f "/tmp/${tarball}"; \
    node --version; \
    npm --version; \
    npm install -g yarn@1.22.22; \
    rm -rf /var/lib/apt/lists/*

# Create frappe user
RUN groupadd -g 1000 frappe \
    && useradd -u 1000 -g frappe -m -s /bin/bash frappe

# ── Stage 2: Build environment ───────────────────────────────
FROM base AS build

# Build deps (compiled Python packages, node-gyp, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    python3-dev \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev \
    pkg-config \
    cron \
    && rm -rf /var/lib/apt/lists/*

# ── Stage 3: Builder (bench init + asset build) ──────────────
FROM build AS builder

# Create /config writable by frappe — uv/filelock writes lock files there
RUN mkdir -p /config && chown frappe:frappe /config

# Copy source into /opt owned by frappe (bench runs as frappe user)
COPY --chown=frappe:frappe . /opt/exe-erp-src

# Initialize git repos — .dockerignore excludes .git, but bench
# requires source dirs to be valid git repos for clone/install.
# Run as frappe since bench init runs as frappe.
USER frappe
RUN cd /opt/exe-erp-src/frappe && \
    git init && git add -A && git -c user.name=build -c user.email=build@exe commit -m "build" --allow-empty && \
    cd /opt/exe-erp-src/apps/erpnext && \
    git init && git add -A && git -c user.name=build -c user.email=build@exe commit -m "build" --allow-empty
WORKDIR /home/frappe

# Install bench
RUN pip install --no-cache-dir --user frappe-bench

# Add local pip bin to PATH
ENV PATH="/home/frappe/.local/bin:${PATH}"

# Restructure source for bench's expected layout:
#   bench expects: apps/frappe/frappe/__init__.py (nested)
#   our fork has:  frappe/__init__.py (flat — frappe dir IS the package)
# Fix: create wrapper directories with the standard bench layout.
# Frappe wrapper: frappe/ is flat (no nested frappe/frappe/), so we
# create a standard layout: ~/frappe-app/setup.py + ~/frappe-app/frappe/
# ERPNext already has standard layout (apps/erpnext/erpnext/__init__.py)
# so it just needs a git init.
RUN mkdir -p ~/frappe-app && \
    ln -s /opt/exe-erp-src/frappe ~/frappe-app/frappe && \
    cp /opt/exe-erp-src/frappe/setup.py ~/frappe-app/setup.py && \
    cp /opt/exe-erp-src/package.json ~/frappe-app/package.json && \
    test -f /opt/exe-erp-src/yarn.lock && cp /opt/exe-erp-src/yarn.lock ~/frappe-app/yarn.lock || true && \
    ln -sf /opt/exe-erp-src/esbuild ~/frappe-app/esbuild && \
    printf '[project]\nname = "frappe"\nversion = "17.0.0"\n' > ~/frappe-app/pyproject.toml && \
    cd ~/frappe-app && git init && git add -A && \
    git -c user.name=build -c user.email=build@exe commit -m "build" && \
    cd /opt/exe-erp-src/apps/erpnext && \
    git init && git add -A && \
    git -c user.name=build -c user.email=build@exe commit -m "build" --allow-empty

# Initialize bench with restructured Frappe source
RUN bench init frappe-bench \
    --frappe-path ~/frappe-app \
    --skip-redis-config-generation \
    --skip-assets \
    --python python3.14 \
    --no-procfile

WORKDIR /home/frappe/frappe-bench

# Install ERPNext (skip assets — build separately after all deps)
RUN bench get-app --skip-assets /opt/exe-erp-src/apps/erpnext

# Install frappe Python deps from root pyproject.toml.
# The setup.py shim doesn't list deps. We parse pyproject.toml with
# tomllib and pip install each dependency directly.
# Install frappe deps into the bench virtualenv (not --user).
# Skip MySQL deps — exe-erp is Postgres-only.
# Skip only mysqlclient (needs MySQL C libs). Keep PyMySQL (pure Python,
# needed at import time for Frappe's database driver detection).
RUN cd /opt/exe-erp-src && ~/frappe-bench/env/bin/python -c "import tomllib,subprocess,sys; deps=tomllib.load(open('pyproject.toml','rb')).get('project',{}).get('dependencies',[]); skip={'mysqlclient'}; pip=[d for d in deps if 'git+' not in d and not any(d.startswith(s) for s in skip)]; git=[d.split('@ ')[-1] for d in deps if 'git+' in d]; pip and subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir']+pip); [subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir',g]) for g in git]"

# Install Node deps and build production assets.
ENV XDG_CONFIG_HOME=/home/frappe/.config
# FRAPPE_BENCH_ROOT tells esbuild where the bench directory is.
# Without this, esbuild resolves __dirname/../.. which points to /
# and looks for /sites/apps.txt instead of frappe-bench/sites/apps.txt.
ENV FRAPPE_BENCH_ROOT=/home/frappe/frappe-bench
RUN mkdir -p /home/frappe/.config && \
    mkdir -p sites && printf "frappe\nerpnext\n" > sites/apps.txt && \
    bench setup requirements --node && \
    (cd /opt/exe-erp-src && yarn install) && \
    (cd apps/frappe && yarn install 2>/dev/null || true) && \
    bench build --production

# Verify the realtime/websocket runtime deps were actually installed into the
# frappe app's node_modules. The websocket service runs `node apps/frappe/socketio.js`,
# which require()s socket.io, @redis/client and cookie — these MUST be present in
# /opt/exe-erp-src/node_modules so they can be copied into the production image.
# Fail the build loudly here rather than crash-loop the websocket container at runtime
# (regression guard for bug 790794e8 / 35576eed).
RUN cd /opt/exe-erp-src && \
    for m in socket.io @redis/client cookie; do \
        test -d "node_modules/$m" || { echo "FATAL: realtime dep '$m' missing from /opt/exe-erp-src/node_modules — websocket would crash-loop"; exit 1; }; \
    done && \
    echo "OK: realtime runtime deps present (socket.io, @redis/client, cookie)"

# ── Stage 4: Final production image ─────────────────────────
FROM base AS production

# Copy frappe-bench from builder
COPY --from=builder --chown=frappe:frappe /home/frappe/frappe-bench /home/frappe/frappe-bench
COPY --from=builder --chown=frappe:frappe /home/frappe/.local /home/frappe/.local

# Copy entrypoint
COPY --chown=frappe:frappe ./entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Add local pip bin to PATH + set SITES_PATH for Frappe site/log resolution
ENV PATH="/home/frappe/.local/bin:${PATH}" \
    SITES_PATH="/home/frappe/frappe-bench/sites"

# Fix broken symlinks: builder stage creates symlinks pointing to
# /opt/exe-erp-src/ which doesn't exist in production. Remove the dead
# symlinks, then copy actual source from builder into the correct locations.
RUN rm -f /home/frappe/frappe-bench/apps/frappe/frappe \
    && rm -f /home/frappe/frappe-bench/apps/frappe/esbuild
COPY --from=builder --chown=frappe:frappe /opt/exe-erp-src/frappe /home/frappe/frappe-bench/apps/frappe/frappe
COPY --from=builder --chown=frappe:frappe /opt/exe-erp-src/esbuild /home/frappe/frappe-bench/apps/frappe/esbuild
COPY --from=builder --chown=frappe:frappe /opt/exe-erp-src/socketio.js /home/frappe/frappe-bench/apps/frappe/socketio.js
COPY --from=builder --chown=frappe:frappe /opt/exe-erp-src/node_utils.js /home/frappe/frappe-bench/apps/frappe/node_utils.js
COPY --from=builder --chown=frappe:frappe /opt/exe-erp-src/realtime /home/frappe/frappe-bench/apps/frappe/realtime
COPY --from=builder --chown=frappe:frappe /opt/exe-erp-src/package.json /home/frappe/frappe-bench/apps/frappe/package.json

# The websocket service runs `node apps/frappe/socketio.js`, which require()s
# socket.io / @redis/client / cookie. Node resolves these by walking up from
# apps/frappe/, so the realtime node_modules MUST sit at apps/frappe/node_modules.
# The bulk frappe-bench COPY above does NOT include them (apps/frappe yarn install
# is best-effort and writes to a different tree), so copy the verified node_modules
# from the source install explicitly. Without this the websocket container
# crash-loops on MODULE_NOT_FOUND (bug 790794e8 / 35576eed).
COPY --from=builder --chown=frappe:frappe /opt/exe-erp-src/node_modules /home/frappe/frappe-bench/apps/frappe/node_modules

# Final regression guard: the realtime entrypoint + its runtime deps must be
# present and resolvable in the production image, or the websocket service is
# undeployable. Fail the build instead of shipping a broken image (bug 790794e8).
RUN test -f /home/frappe/frappe-bench/apps/frappe/socketio.js \
    && test -f /home/frappe/frappe-bench/apps/frappe/node_utils.js \
    && test -f /home/frappe/frappe-bench/apps/frappe/realtime/index.js \
    && test -d /home/frappe/frappe-bench/apps/frappe/node_modules/socket.io \
    && test -d /home/frappe/frappe-bench/apps/frappe/node_modules/@redis/client \
    && test -d /home/frappe/frappe-bench/apps/frappe/node_modules/cookie \
    && echo "OK: websocket entrypoint + realtime deps shipped to production image"

# Fix editable installs: pip install from setup.py misses nested packages
# (page_renderers, etc.) because frappe uses a flat layout without proper
# find_packages. Instead: remove the broken editable finders and add .pth
# files that point to the apps source directories (how bench works in dev).
RUN SITE_PKG=$(find /home/frappe/frappe-bench/env/lib -name site-packages -type d) \
    && rm -f ${SITE_PKG}/__editable__*frappe* \
    && rm -rf ${SITE_PKG}/frappe ${SITE_PKG}/frappe-*.dist-info \
    && rm -rf ${SITE_PKG}/erpnext ${SITE_PKG}/erpnext-*.dist-info \
    && rm -f ${SITE_PKG}/__editable__*erpnext* ${SITE_PKG}/erpnext.pth \
    && echo "/home/frappe/frappe-bench/apps/frappe" > $(find /home/frappe/frappe-bench/env/lib -name site-packages -type d)/frappe.pth \
    && echo "/home/frappe/frappe-bench/apps/erpnext" > $(find /home/frappe/frappe-bench/env/lib -name site-packages -type d)/erpnext.pth

# Create sites directory with correct ownership
RUN mkdir -p /home/frappe/frappe-bench/sites \
    && chown -R frappe:frappe /home/frappe/frappe-bench/sites

# Nginx config for static files
RUN rm -f /etc/nginx/sites-enabled/default
COPY --from=builder /home/frappe/frappe-bench/sites/assets /home/frappe/frappe-bench/sites/assets

# Fix broken asset symlinks (same issue as app symlinks — builder paths)
RUN rm -f /home/frappe/frappe-bench/sites/assets/frappe \
    && ln -s /home/frappe/frappe-bench/apps/frappe/frappe/public /home/frappe/frappe-bench/sites/assets/frappe

# Back up prebuilt assets OUTSIDE the sites/ tree (bug 29a22993).
# At runtime, docker-compose mounts the erp-sites named volume over
# /home/frappe/frappe-bench/sites, which SHADOWS the baked-in sites/assets on a
# fresh volume — assets vanish and the desk UI 404s on CSS/JS. /opt is never a
# volume mount, so this copy survives; the entrypoint restores it into the
# volume on boot when sites/assets is missing or empty.
RUN mkdir -p /opt/exe-erp-assets \
    && cp -a /home/frappe/frappe-bench/sites/assets/. /opt/exe-erp-assets/ \
    && chown -R frappe:frappe /opt/exe-erp-assets \
    && test -f /opt/exe-erp-assets/assets.json \
    && echo "OK: prebuilt assets backed up to /opt/exe-erp-assets (volume-shadow safe)"

# Copy WSGI wrapper (TracingMiddleware)
COPY --chown=frappe:frappe ./wsgi.py /home/frappe/frappe-bench/wsgi.py

WORKDIR /home/frappe/frappe-bench

USER frappe

EXPOSE 8000 9000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Default: run gunicorn (backend)
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--worker-tmp-dir", "/dev/shm", \
     "wsgi:application"]
