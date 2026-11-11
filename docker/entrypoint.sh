#!/bin/sh
# Container entrypoint.
#
# Platform volumes (Fly, Render) are usually mounted owned by root, so the app
# user could not write to them. When we start as root, fix the ownership of the
# data directory, then drop to the unprivileged app user for the actual server.
# The server process itself never runs as root.
set -e

if [ "$(id -u)" = "0" ]; then
    mkdir -p "${CURRICULUM_HOSTED_DIR:-/data/hosted}"
    chown -R 10001:10001 "${CURRICULUM_DATA_DIR:-/data}"
    exec setpriv --reuid=10001 --regid=10001 --init-groups "$@"
fi

exec "$@"
