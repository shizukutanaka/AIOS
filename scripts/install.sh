#!/usr/bin/env bash
# AI Native Linux OS — One-line installer
# curl -sSL https://raw.githubusercontent.com/shizukutanaka/aios/main/scripts/install.sh | bash
set -euo pipefail

REPO="https://github.com/shizukutanaka/aios"
INSTALL_DIR="/opt/aios"
BIN_LINK="/usr/local/bin/aictl"

info()  { echo -e "\033[0;32m✓\033[0m $*"; }
warn()  { echo -e "\033[0;33m⚠\033[0m $*"; }
err()   { echo -e "\033[0;31m✗\033[0m $*"; exit 1; }

echo
echo "  AI Native Linux OS — Installer"
echo "  ================================"
echo

# Check Python
PYTHON=""
for p in python3.13 python3.12 python3.11 python3; do
    if command -v "$p" &>/dev/null; then
        ver=$("$p" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON="$p"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    err "Python 3.11+ required. Install: sudo dnf install python3 (or apt install python3)"
fi
info "Python: $PYTHON ($($PYTHON --version 2>&1))"

# Check git
if ! command -v git &>/dev/null; then
    warn "git not found — installing from tarball instead"
    USE_GIT=false
else
    USE_GIT=true
fi

# Install
if [ "$USE_GIT" = true ]; then
    if [ -d "$INSTALL_DIR" ]; then
        info "Updating existing installation..."
        cd "$INSTALL_DIR" && git pull --quiet
    else
        info "Cloning repository..."
        sudo git clone --depth 1 "$REPO.git" "$INSTALL_DIR" 2>/dev/null || \
            git clone --depth 1 "$REPO.git" "$INSTALL_DIR"
    fi
else
    info "Downloading release..."
    TMP=$(mktemp -d)
    curl -sSL "$REPO/archive/refs/heads/main.tar.gz" | tar xz -C "$TMP"
    sudo mkdir -p "$INSTALL_DIR"
    sudo cp -r "$TMP"/aios-main/* "$INSTALL_DIR/"
    rm -rf "$TMP"
fi

# Create wrapper script
sudo tee "$BIN_LINK" > /dev/null << 'EOF'
#!/bin/bash
export PYTHONPATH=/opt/aios:${PYTHONPATH:-}
exec python3 -m aictl "$@"
EOF
sudo chmod +x "$BIN_LINK"
info "Installed: $BIN_LINK"

# Verify
if aictl --help &>/dev/null; then
    info "Installation verified"
else
    warn "Installation completed but aictl --help failed"
fi

# Post-install checks
echo
echo "  Post-install:"

if command -v podman &>/dev/null; then
    info "Podman: $(podman --version 2>/dev/null | head -1)"
else
    warn "Podman not found — install: sudo dnf install podman"
fi

if command -v nvidia-smi &>/dev/null; then
    info "NVIDIA GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
else
    echo "  · No NVIDIA GPU detected (CPU-only mode)"
fi

if command -v ollama &>/dev/null; then
    info "Ollama: $(ollama --version 2>/dev/null)"
else
    echo "  · Ollama not found — install: curl -fsSL https://ollama.com/install.sh | sh"
fi

echo
echo "  Quick start:"
echo "    aictl init"
echo "    aictl doctor"
echo "    aictl recipe run local-chat"
echo
