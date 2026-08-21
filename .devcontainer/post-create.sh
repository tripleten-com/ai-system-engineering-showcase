#!/usr/bin/env bash
set -euo pipefail

echo "=========================================================="
echo "Initializing TripleTen Showcase Codespace Environment"
echo "=========================================================="

# 1. Provide .env if not present
if [ ! -f .env ]; then
  echo "==> Creating .env from .env.example..."
  cp .env.example .env
fi

# 2. Ensure uv is installed
if ! command -v uv &> /dev/null; then
  echo "==> Installing uv package manager..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

# 3. Sync Python workspace dependencies
echo "==> Syncing Python workspace dependencies with uv..."
uv sync --frozen

# 4. Install Node dependencies (root and War Room frontend)
echo "==> Installing Node dependencies..."
npm ci
npm --prefix services/incident-war-room ci

echo "=========================================================="
echo " Codespace Environment Ready!"
echo " Start the 9-container stack with: docker compose up -d"
echo " Or run tests with: uv run poe test"
echo "=========================================================="
