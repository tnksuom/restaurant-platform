#!/bin/sh
# Prepare the upload folders on the persistent disk, then run as the unprivileged "app" user.
set -e
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$PRIVATE_UPLOAD_ROOT" "$PUBLIC_UPLOAD_ROOT"
  chown app:app "$PRIVATE_UPLOAD_ROOT" "$PUBLIC_UPLOAD_ROOT"
  chmod 750 "$PRIVATE_UPLOAD_ROOT" "$PUBLIC_UPLOAD_ROOT"
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
