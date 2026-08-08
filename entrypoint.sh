#!/usr/bin/env bash
set -euo pipefail

: "${HOST_UID:?HOST_UID not set}"
: "${HOST_GID:?HOST_GID not set}"
: "${HOST_UNAME:?HOST_UNAME not set}"
: "${HOST_GNAME:?HOST_GNAME not set}"

if ! getent group "$HOST_GNAME" >/dev/null; then
    groupadd -g "$HOST_GID" "$HOST_GNAME"
fi

if ! id "$HOST_UNAME" >/dev/null 2>&1; then
    useradd \
        --uid "$HOST_UID" \
        --gid "$HOST_GID" \
        --create-home \
        --shell /bin/bash \
        "$HOST_UNAME"
fi

chown -R "$HOST_UNAME:$HOST_GNAME" /app

exec gosu "$HOST_UNAME" /app/startup.sh