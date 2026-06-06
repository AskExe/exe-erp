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

# Initialize bench with local Frappe source
RUN bench init frappe-bench \
    --frappe-path /opt/exe-erp-src/frappe \
    --skip-redis-config-generation \
    --skip-assets \
    --python python3.14 \
    --no-procfile

WORKDIR /home/frappe/frappe-bench

# Install ERPNext from local source
RUN bench get-app /opt/exe-erp-src/apps/erpnext

# Install Python deps for all apps
RUN bench setup requirements --python

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
