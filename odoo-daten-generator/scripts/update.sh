#!/usr/bin/env bash
# Pull-based auto-update for the Docker deployment. Meant to run from cron on
# the deploy host (Mac or Pi5) — the host pulls, the container never reaches
# out on its own. No inbound port needs to be opened for this to work.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"      # odoo-daten-generator/
REPO_ROOT="$(dirname "$APP_DIR")"       # git repo root

LOG_FILE="${ODOO_GENERATOR_UPDATE_LOG:-$APP_DIR/update.log}"
LOCK_FILE="${ODOO_GENERATOR_UPDATE_LOCK:-/tmp/odoo-demogen-update.lock}"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG_FILE"; }

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    log "Update already running (lock held) - skipping this run"
    exit 0
fi

cd "$REPO_ROOT"

BEFORE="$(git rev-parse HEAD)"
if ! git pull --ff-only >>"$LOG_FILE" 2>&1; then
    log "git pull --ff-only failed - local checkout diverged or network down, aborting"
    exit 1
fi
AFTER="$(git rev-parse HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
    log "Already up to date ($BEFORE)"
    exit 0
fi

log "Updated $BEFORE -> $AFTER, rebuilding container"

cd "$APP_DIR"
if docker compose up -d --build >>"$LOG_FILE" 2>&1; then
    log "Rebuild successful"
else
    log "Rebuild FAILED - container may be running the old image, investigate manually"
    exit 1
fi
