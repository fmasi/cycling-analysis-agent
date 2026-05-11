#!/usr/bin/env bash
# Idempotent installer for Miniconda + the `cycling` env inside an OpenClaw
# container. Safe to run any time:
#   - If Miniconda is missing, downloads + installs it under /opt/miniconda3.
#   - If the `cycling` env is missing, creates it from environment.yml.
#   - If the env exists, runs `conda env update` to pick up any new deps.
#
# Designed to be called from inside the OpenClaw container after each
# `docker compose up -d`:
#   docker compose exec openclaw-gateway bash \
#     /home/node/.openclaw/workspace/cycling/cycling-coach/scripts/setup-container-conda.sh
#
# When persisted via the `cycling-miniconda` named volume in
# docker-compose.override.yml, repeat invocations are essentially no-ops
# (Miniconda install is skipped; env update is fast).
set -euo pipefail

CONDA_ROOT=/opt/miniconda3
ENV_NAME=cycling
WORKSPACE_DEFAULT=/home/node/.openclaw/workspace/cycling/cycling-coach
WORKSPACE=${CYCLING_WORKSPACE:-$WORKSPACE_DEFAULT}
ENV_FILE="$WORKSPACE/environment.yml"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: environment.yml not found at $ENV_FILE"
  echo "Set CYCLING_WORKSPACE to override the workspace path."
  exit 1
fi

# Step 1: Miniconda
if [ ! -x "$CONDA_ROOT/bin/conda" ]; then
  echo "Installing Miniconda to $CONDA_ROOT ..."
  arch=$(uname -m)
  case "$arch" in
    x86_64)  url="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh" ;;
    aarch64) url="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh" ;;
    *) echo "ERROR: unsupported architecture: $arch"; exit 1 ;;
  esac
  tmp=$(mktemp -t miniconda.XXXXXX.sh)
  curl -fsSL "$url" -o "$tmp"
  bash "$tmp" -b -u -p "$CONDA_ROOT"
  rm -f "$tmp"
else
  echo "Miniconda already at $CONDA_ROOT — skipping install."
fi

CONDA="$CONDA_ROOT/bin/conda"

# Step 2: the cycling env
if "$CONDA" env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "Updating existing '$ENV_NAME' env from $ENV_FILE ..."
  "$CONDA" env update -n "$ENV_NAME" -f "$ENV_FILE"
else
  echo "Creating '$ENV_NAME' env from $ENV_FILE ..."
  "$CONDA" env create -n "$ENV_NAME" -f "$ENV_FILE"
fi

# Step 3: quick smoke check
echo
echo "=== Sanity check ==="
"$CONDA_ROOT/envs/$ENV_NAME/bin/python" -c "
import rasterio, pyproj, requests, py7zr, shapefile, numpy, scipy, matplotlib
print('All core deps importable.')
print(f'rasterio {rasterio.__version__}, py7zr {py7zr.__version__}, pyshp {shapefile.__version__}')
"

echo
echo "Done. Use /opt/miniconda3/envs/$ENV_NAME/bin/python to run framework scripts."
