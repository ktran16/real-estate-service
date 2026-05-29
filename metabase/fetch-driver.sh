#!/usr/bin/env bash
# Download the MotherDuck Metabase DuckDB driver into ./plugins.
# Release 1.5.3.0 bundles DuckDB 1.5.3 (matches our .duckdb file) and targets
# Metabase 59.x (see docker-compose.yml image tag).
set -euo pipefail

VERSION="1.5.3.0"
JAR="duckdb.metabase-driver.jar"
URL="https://github.com/MotherDuck-Open-Source/metabase_duckdb_driver/releases/download/${VERSION}/${JAR}"

BASE="$(cd "$(dirname "$0")" && pwd)"
DIR="${BASE}/plugins"
mkdir -p "$DIR"

# Pre-create the dirs the container bind-mounts and make them world-writable.
# The Metabase java process runs as uid 2000 ("metabase") and MUST be able to
# write to BOTH dirs: it scans/extracts driver jars in the plugins dir and
# writes its H2 app DB to metabase-data. If Docker creates these dirs itself
# (root-owned) or they're owned by another uid without the write bit, Metabase
# silently falls back to /tmp and never loads the DuckDB jar — the driver then
# never appears in the engine list. 0777 keeps it working regardless of uid.
mkdir -p "${BASE}/metabase-data"
chmod 0777 "$DIR" "${BASE}/metabase-data"

echo "Downloading DuckDB driver ${VERSION} -> ${DIR}/${JAR}"
curl -fL -o "${DIR}/${JAR}" "$URL"
echo "Done. Restart Metabase to load the driver:  docker compose restart metabase"
