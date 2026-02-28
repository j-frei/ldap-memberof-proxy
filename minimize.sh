#!/bin/bash
set -e

# This script removes all non-essential files from the repository
# (useful to integrate the code into an existing project)

# Change CWD to script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Declare essential files to keep
ESSENTIAL_FILES=(
    "Dockerfile"
    "proxy.py"
    "requirements.txt"
    ".dockerignore"
    "LICENSE"
)

# Remove all files and directories except the essential ones
for item in * .[^.]*; do
    if [[ ! " ${ESSENTIAL_FILES[*]} " =~ " ${item} " ]]; then
        echo "Removing $item..."
        rm -rf "$item"
    fi
done
