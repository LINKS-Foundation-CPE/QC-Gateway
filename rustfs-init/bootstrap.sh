#!/bin/sh
# Bootstrap the RustFS object store: bucket, application user, anonymous
# read-by-key policy, CORS, and the end of the migration from MinIO. Every step is
# idempotent; this runs on every `docker compose up`.
set -eu

STORE_ENDPOINT="${STORE_ENDPOINT:-http://rustfs:9000}"
export HOME=/tmp

echo "Waiting for RustFS at $STORE_ENDPOINT..."
i=0
until rc alias set local "$STORE_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1 \
  && rc ls local >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "RustFS did not answer at $STORE_ENDPOINT after 60 s" >&2
    exit 1
  fi
  sleep 2
done

rc bucket create --ignore-existing "local/$BUCKET_NAME" >/dev/null

if rc admin user info local "$APP_USER" >/dev/null 2>&1; then
  echo "User $APP_USER already exists."
else
  rc admin user add local "$APP_USER" "$APP_PASSWORD" >/dev/null
  echo "Created user $APP_USER."
fi

POLICY_FILE="/tmp/${APP_USER}-policy.json"
cat > "$POLICY_FILE" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:*"],
      "Resource": [
        "arn:aws:s3:::$BUCKET_NAME",
        "arn:aws:s3:::$BUCKET_NAME/*"
      ]
    }
  ]
}
EOF
rc admin policy create local "${APP_USER}-policy" "$POLICY_FILE" >/dev/null
rc admin policy attach local "${APP_USER}-policy" --user "$APP_USER" >/dev/null

# Anonymous access: read an object by its exact key, and nothing else. No
# canned preset: RustFS's `download`, like MinIO's, also grants s3:ListBucket,
# which would let anyone list every user's e-mail address and job id. Nothing
# in the stack lists anonymously — the job portal, the dashboard and the
# calibration index all fetch files by known names.
ANON_POLICY_FILE="/tmp/${BUCKET_NAME}-anonymous.json"
cat > "$ANON_POLICY_FILE" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"AWS": ["*"]},
      "Action": ["s3:GetObject"],
      "Resource": ["arn:aws:s3:::$BUCKET_NAME/*"]
    }
  ]
}
EOF
rc bucket anonymous set-json "$ANON_POLICY_FILE" "local/$BUCKET_NAME" >/dev/null

# CORS: the job portal (jobs.*) fetches these files from another origin
# (store.*). MinIO allowed any origin by default; RustFS sends no CORS headers
# until the bucket has rules, and without them the browser blocks every fetch.
# Any origin, read-only: no more than anonymous access already allows. `set`
# replaces the bucket's rules.
CORS_FILE="/tmp/${BUCKET_NAME}-cors.xml"
cat > "$CORS_FILE" <<EOF
<CORSConfiguration>
  <CORSRule>
    <AllowedOrigin>*</AllowedOrigin>
    <AllowedMethod>GET</AllowedMethod>
    <AllowedMethod>HEAD</AllowedMethod>
    <AllowedHeader>*</AllowedHeader>
    <ExposeHeader>ETag</ExposeHeader>
    <ExposeHeader>Content-Length</ExposeHeader>
    <ExposeHeader>Content-Type</ExposeHeader>
    <ExposeHeader>Last-Modified</ExposeHeader>
    <MaxAgeSeconds>3600</MaxAgeSeconds>
  </CORSRule>
</CORSConfiguration>
EOF
rc bucket cors set "local/$BUCKET_NAME" "$CORS_FILE" >/dev/null

echo "Bucket '$BUCKET_NAME' and user '$APP_USER' ensured."

# ── Retiring the migration from MinIO ───────────────────────────────────────
#
# The previous release served, from MinIO, any key RustFS did not have yet,
# and backfilled the rest. This release no longer runs MinIO, so RustFS must
# stop pointing at it — but only once the backfill has copied everything.
# Before that, the objects it has not reached exist only in MinIO's directory:
# refuse, loudly, rather than turn an unfinished migration into missing results.
TARGET="local/$BUCKET_NAME"
MINIO_BUCKET_DIR="/storage/data/$BUCKET_NAME"
OLD_DIR="${MINIO_STORAGE_PATH:-MINIO_STORAGE_PATH}/data"

# No job is an error on stderr and nothing on stdout: both read as "none".
# The job record outlives the migration configuration, so this still says
# "completed" on every run after the configuration has been removed.
status=$(rc admin bucket migration backfill status "$TARGET" --json 2>/dev/null || true)
state=$(printf '%s' "$status" | jq -r '.data.result.job.state // empty' 2>/dev/null || true)
failed=$(printf '%s' "$status" | jq -r '.data.result.job.failed // empty' 2>/dev/null || true)
state="${state:-none}"
failed="${failed:-0}"
migrated=false
if [ "$state" = completed ] && [ "$failed" -eq 0 ]; then
  migrated=true
fi

if rc admin bucket migration get "$TARGET" >/dev/null 2>&1; then
  if [ "$migrated" = false ]; then
    cat >&2 <<EOM
Migration from MinIO is not finished (backfill: $state, $failed failed).
Objects it has not copied yet are only in $OLD_DIR, and this
release no longer runs MinIO to serve them. Redeploy the previous release and
wait for rustfs-init to report "Backfill completed, nothing failed" first.
EOM
    exit 1
  fi
  rc admin bucket migration rm "$TARGET" >/dev/null
  echo "Migration from MinIO complete; RustFS no longer points at it."
fi

# A MinIO data directory still holding the bucket is either the copy a finished
# migration left behind, or the only copy of a deployment that never migrated.
if [ -d "$MINIO_BUCKET_DIR" ]; then
  if [ "$migrated" = true ]; then
    echo "Old MinIO data is still in $OLD_DIR; everything in it is in RustFS, so it can be deleted."
  else
    cat >&2 <<EOM
$OLD_DIR holds a MinIO bucket '$BUCKET_NAME' that was never
migrated to RustFS: its objects are not being served. Deploy the previous
release, which migrates them, and wait for rustfs-init to report "Backfill
completed, nothing failed" before deploying this one.
EOM
    exit 1
  fi
fi
