#!/usr/bin/env bash
#
# PrismClaw PostgreSQL backup script.
#
# Creates a compressed pg_dump of the PrismClaw database, rotates local copies
# (keep last 7 days), and optionally uploads to S3 when S3_BUCKET is set.
#
# Usage:
#   bash scripts/pg_backup.sh            # real run
#   bash scripts/pg_backup.sh --dry-run  # print commands, do nothing
#
# Environment (reads from env or .env-style exports):
#   PGHOST (default: localhost)
#   PGPORT (default: 5432)
#   PGUSER (default: postgres)
#   PGPASSWORD
#   PGDATABASE (default: prismclaw)
#   BACKUP_DIR (default: ./backups)
#   RETENTION_DAYS (default: 7)
#   S3_BUCKET (optional; e.g. s3://my-bucket/prismclaw)
#   AWS_REGION (optional)

set -euo pipefail

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-postgres}"
PGDATABASE="${PGDATABASE:-prismclaw}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="${BACKUP_DIR}/prismclaw-${TIMESTAMP}.sql.gz"

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] $*"
  else
    eval "$@"
  fi
}

echo "PrismClaw PG backup — $([ "$DRY_RUN" -eq 1 ] && echo DRY-RUN || echo LIVE)"
echo "  host=${PGHOST}:${PGPORT} db=${PGDATABASE} user=${PGUSER}"
echo "  backup_dir=${BACKUP_DIR} retention_days=${RETENTION_DAYS}"

run "mkdir -p \"${BACKUP_DIR}\""

# 1. Dump + compress
run "PGPASSWORD=\"${PGPASSWORD:-}\" pg_dump -h \"${PGHOST}\" -p \"${PGPORT}\" -U \"${PGUSER}\" -d \"${PGDATABASE}\" -Fc -Z 0 | gzip > \"${OUT_FILE}\""

# 2. Local rotation (delete files older than RETENTION_DAYS)
echo "Rotating local backups older than ${RETENTION_DAYS} days..."
if [ "$DRY_RUN" -eq 1 ]; then
  echo "[dry-run] find \"${BACKUP_DIR}\" -name 'prismclaw-*.sql.gz' -mtime +${RETENTION_DAYS} -delete"
else
  find "${BACKUP_DIR}" -name 'prismclaw-*.sql.gz' -mtime +"${RETENTION_DAYS}" -delete 2>/dev/null || true
fi

# 3. Optional S3 upload
if [ -n "${S3_BUCKET:-}" ]; then
  echo "Uploading to S3: ${S3_BUCKET}"
  run "aws s3 cp \"${OUT_FILE}\" \"${S3_BUCKET}/\" --region \"${AWS_REGION:-us-east-1}\""
else
  echo "S3_BUCKET not set — skipping upload."
fi

echo "Backup complete: ${OUT_FILE}"
