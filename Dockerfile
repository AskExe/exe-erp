# ──────────────────────────────────────────────────────────────
# Exe ERP — Production Dockerfile (multi-stage)
# Based on frappe/frappe_docker Containerfile pattern
# Postgres-only, no MariaDB
# ──────────────────────────────────────────────────────────────

# ── Stage 1: Base runtime ────────────────────────────────────
FROM python:3.14-slim-bookworm AS base

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
    # Locale
    locales \
    && sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen \
    && locale-gen en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8

# Install Node.js 24 via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g yarn \
    && rm -rf /var/lib/apt/lists/*

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

# Install frappe Python deps from the real pyproject.toml.
# The setup.py shim doesn't include install_requires — the actual
# dependencies live in /opt/exe-erp-src/pyproject.toml.
# pip can install directly from a directory with pyproject.toml.
RUN pip install --no-cache-dir --user /opt/exe-erp-src/

# Install Node deps and build production assets
RUN bench setup requirements --node \
    && bench build --production

# ── Stage 4: Final production image ─────────────────────────
FROM base AS production

# Copy frappe-bench from builder
COPY --from=builder --chown=frappe:frappe /home/frappe/frappe-bench /home/frappe/frappe-bench
COPY --from=builder --chown=frappe:frappe /home/frappe/.local /home/frappe/.local

# Copy entrypoint
COPY --chown=frappe:frappe ./entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Add local pip bin to PATH
ENV PATH="/home/frappe/.local/bin:${PATH}"

# Create sites directory with correct ownership
RUN mkdir -p /home/frappe/frappe-bench/sites \
    && chown -R frappe:frappe /home/frappe/frappe-bench/sites

# Nginx config for static files
RUN rm -f /etc/nginx/sites-enabled/default
COPY --from=builder /home/frappe/frappe-bench/sites/assets /home/frappe/frappe-bench/sites/assets

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
     "frappe.app:application"]
