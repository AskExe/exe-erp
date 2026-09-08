#!/usr/bin/env bash
# Removes stale buildx builder containers left behind by a previous,
# cancelled run of *this workflow* (release-stack-image.yml's
# release-image job) -- and nothing else.
#
# Bug df3264de: exe-erp, exe-crm and exe-os release runners now share one
# physical Mac and one Docker host (the lima ci-linux VM). This step used
# to filter on `docker/setup-buildx-action`'s AUTO-GENERATED builder name
# ("builder-<random>"), which carries no repo identity at all -- every
# repo that doesn't pass an explicit `name:` gets a container matching the
# exact same "buildx_buildkit_builder-*" pattern. On a shared host that
# pattern is satisfied just as often by another repo's ACTIVE builder as
# by a real orphan, and the old step force-removed (`docker rm -f`)
# whatever it found. That SIGKILLed a concurrent exe-crm release
# mid-build (verified 2026-09-08, run 34177871028).
#
# THE FIX: this job's "Set up Docker Buildx" step (release-stack-image.yml)
# now pins an explicit, workflow-exclusive builder name --
# BUILDX_BUILDER_NAME below -- instead of accepting the auto-generated
# one. No other repo or workflow in this organization ever creates a
# container with this exact name, so matching on it is a POSITIVE
# ownership check ("I only delete what I created"), not a narrowed guess:
# any container this predicate matches was, by construction, created by
# a past run of this same job. Anything that does not carry this exact
# name -- another repo's builder, a hand-created container, anything
# whose ownership cannot be positively established -- is left alone,
# unconditionally. That is the fail-safe: absence of proof of ownership
# is never treated as permission to delete.
#
# This job's runner label maps to one job at a time on the physical
# runner (see release-stack-image.yml), so a stable (not per-run) name is
# safe: two attempts of *this* job can never be building concurrently on
# the same host, only sequentially -- which is exactly the "leaked by an
# earlier, already-finished-or-cancelled run" case this cleanup exists
# to handle (bug 5f2fb4fa).
set -uo pipefail

# Single source of truth: release-stack-image.yml's "Set up Docker Buildx"
# step must pass this exact same name so the container this predicate is
# allowed to remove is always the one this job itself created previously.
BUILDX_BUILDER_NAME="${BUILDX_BUILDER_NAME:-erp-release-buildx}"
CONTAINER_PREFIX="buildx_buildkit_${BUILDX_BUILDER_NAME}"

# Anchored: a match must START with our exclusive prefix, never merely
# contain it, and never fall back to matching anything broader.
orphans="$(docker ps -aq --filter "name=^${CONTAINER_PREFIX}" 2>/dev/null || true)"

if [ -n "${orphans}" ]; then
	echo "Removing this job's own orphaned buildx builder container(s) from a prior run (name prefix: ${CONTAINER_PREFIX}):"
	docker ps -a --filter "name=^${CONTAINER_PREFIX}" --format '  {{.Names}} ({{.Status}})'
	docker rm -f ${orphans} >/dev/null 2>&1 || true
else
	echo "No orphaned buildx builder containers owned by this job (name prefix: ${CONTAINER_PREFIX}) found."
fi
