#!/bin/sh
set -e

echo "🚀 Waiting for MinIO to start..."
sleep 10

echo "⚙️ Bootstrapping MinIO..."

mc alias set local http://minio-job-data:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

# ✅ Bucket creation (already idempotent)
mc mb --ignore-existing local/$BUCKET_NAME

# ✅ Check if user exists before creating
if ! mc admin user info local "$APP_USER" >/dev/null 2>&1; then
  echo "👤 Creating application user: $APP_USER"
  mc admin user add local "$APP_USER" "$APP_PASSWORD"
else
  echo "ℹ️ User $APP_USER already exists, skipping creation."
fi

# ✅ Create/overwrite policy safely
POLICY_FILE=/tmp/${APP_USER}-policy.json
cat > $POLICY_FILE <<EOF
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

# Add or update the policy
mc admin policy create local ${APP_USER}-policy $POLICY_FILE

# Attach policy to user (safe to re-run)
mc admin policy attach local ${APP_USER}-policy --user $APP_USER

# Ensure anonymous access is set for the bucket
mc anonymous set download local/$BUCKET_NAME

echo "✅ Bucket '$BUCKET_NAME' and user '$APP_USER' ensured."
