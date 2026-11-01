#!/bin/zsh
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
LAUNCHER="$ROOT/iScreeM Launcher.app/Contents/MacOS/iScreeM Launcher"
LOG_DIR="$HOME/Library/Logs/iScreeM"
mkdir -p "$LOG_DIR"

# Detach the long-running supervisor from this short-lived Terminal command.
# It retains Terminal's user-approved access to the project in Documents.
nohup "$LAUNCHER" --terminal-supervisor </dev/null >>"$LOG_DIR/launcher.log" 2>&1 &!
