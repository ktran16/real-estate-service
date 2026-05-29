#!/usr/bin/env bash
# Download the MotherDuck Metabase DuckDB driver into ./plugins.
# Release 1.5.3.0 bundles DuckDB 1.5.3 (matches our .duckdb file) and targets
# Metabase 59.x (see docker-compose.yml image tag).
set -euo pipefail

VERSION="1.5.3.0"
JAR="duckdb.metabase-driver.jar"
URL="https://github.com/MotherDuck-Open-Source/metabase_duckdb_driver/releases/download/${VERSION}/${JAR}"

DIR="$(cd "$(dirname "$0")" && pwd)/plugins"
mkdir -p "$DIR"

echo "Downloading DuckDB driver ${VERSION} -> ${DIR}/${JAR}"
curl -fL -o "${DIR}/${JAR}" "$URL"
echo "Done. Restart Metabase to load the driver:  docker compose restart metabase"
