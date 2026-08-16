#!/usr/bin/env bash
set -euo pipefail

# Usage:
# TWINE_PASSWORD="pypi-..." ./scripts/upload_to_pypi.sh

if [ -z "${TWINE_PASSWORD:-}" ]; then
  echo "Error: set TWINE_PASSWORD environment variable to your PyPI API token"
  echo "Example: TWINE_PASSWORD=\"pypi-...\" ./scripts/upload_to_pypi.sh"
  exit 1
fi

export TWINE_USERNAME="__token__"

echo "Cleaning old builds..."
rm -rf dist

echo "Building distributions..."
python -m build

echo "Checking artifacts with twine..."
python -m twine check dist/*

version=$(grep -E '^version\s*=\s*"' pyproject.toml | sed -E 's/version\s*=\s*"([^"]+)"/\1/')
if [ -z "$version" ]; then
  echo "Could not determine version from pyproject.toml" >&2
  exit 1
fi
echo "Version detected: $version"

files=(dist/*-$version*)
if [ ${#files[@]} -eq 0 ]; then
  echo "No dist files found for version $version" >&2
  exit 1
fi

echo "Uploading to PyPI (only files for version $version)..."
python -m twine upload --verbose --skip-existing "${files[@]}"

echo "Done."
