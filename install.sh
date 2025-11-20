#!/usr/bin/env bash
set -e

APP_NAME="onigiri"

# Where the repo is (directory of this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Install locations (XDG-ish)
INSTALL_DIR="$HOME/.local/share/$APP_NAME"
VENV_DIR="$INSTALL_DIR/venv"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
CONFIG_DIR="$HOME/.config/onigiri"

echo "🔧 Installing $APP_NAME..."

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$CONFIG_DIR"

echo "📁 Copying application files to $INSTALL_DIR"
# Copy everything in your repo except venvs and git stuff
rsync -a --delete \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "venv" \
  "$SCRIPT_DIR/" "$INSTALL_DIR/"

# Install default JSON config if the user doesn't have one yet
DEFAULT_JSON="$INSTALL_DIR/onigiri.json"
TARGET_JSON="$CONFIG_DIR/onigiri.json"

if [ ! -f "$TARGET_JSON" ] && [ -f "$DEFAULT_JSON" ]; then
  echo "📝 No existing config found, installing default profile to $TARGET_JSON"
  cp "$DEFAULT_JSON" "$TARGET_JSON"
fi

# Create venv
if [ ! -d "$VENV_DIR" ]; then
  echo "🐍 Creating virtual environment in $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

echo "📦 Installing Python dependencies into venv"
source "$VENV_DIR/bin/activate"

if [ -f "$INSTALL_DIR/requirements.txt" ]; then
  pip install --upgrade pip
  pip install -r "$INSTALL_DIR/requirements.txt"
else
  echo "⚠️  requirements.txt not found in $INSTALL_DIR – skipping pip install."
fi

deactivate

# Launcher script
LAUNCHER="$BIN_DIR/$APP_NAME"

echo "🚀 Creating launcher script at $LAUNCHER"

cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
APP_DIR="\$HOME/.local/share/$APP_NAME"
VENV_DIR="\$APP_DIR/venv"

if [ ! -d "\$VENV_DIR" ]; then
  echo "$APP_NAME: venv not found at \$VENV_DIR"
  exit 1
fi

source "\$VENV_DIR/bin/activate"

# Launch the UI (change to onigiri.py if you prefer CLI)
exec python "\$APP_DIR/onigiri_ui.py" "\$@"
EOF

chmod +x "$LAUNCHER"

# Desktop entry
DESKTOP_FILE="$DESKTOP_DIR/$APP_NAME.desktop"
echo "🖥️  Creating desktop entry at $DESKTOP_FILE"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Onigiri Tiler
Comment=KWin tiling dashboard for KDE
Exec=$LAUNCHER
Icon=utilities-terminal
Terminal=false
Categories=Utility;System;
StartupNotify=false
EOF

chmod +x "$DESKTOP_FILE"

echo
echo "✅ Installation complete."

echo "➡  You can now run it via:"
echo "   $APP_NAME"
echo "   (or find 'Onigiri Tiler' in your application launcher)"
