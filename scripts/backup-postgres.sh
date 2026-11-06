#!/usr/bin/env bash
set -euo pipefail

# DATABASE_URL and BACKUP_DIR must be supplied by the operator or scheduler.
: "${DATABASE_URL:?Set DATABASE_URL to the PostgreSQL connection URL}"
: "${BACKUP_DIR:?Set BACKUP_DIR to a durable backup directory}"
mkdir -p "$BACKUP_DIR"
umask 077
backup_file="$BACKUP_DIR/k8s-lab-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --format=custom --no-owner --file="$backup_file" "$DATABASE_URL"
printf 'Backup written to %s\n' "$backup_file"
