#!/usr/bin/env bash
set -e

APP_NAME="onigiri"

# Where the repo is (directory of this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Install locations
INSTALL_DIR="$HOME/.local/share/$APP_NAME"
VENV_DIR="$INSTALL_DIR/venv"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
CONFIG_DIR="$HOME/.config/onigiri"

echo "🔧 Installing $APP_NAME..."
echo "  Source:   $SCRIPT_DIR"
echo "  Install:  $INSTALL_DIR"
echo "  Venv:     $VENV_DIR"
echo "  Bin:      $BIN_DIR"
echo "  Desktop:  $DESKTOP_DIR"

# 1) Clean old install
if [ -d "$INSTALL_DIR" ]; then
  echo "⚠️  Removing existing install at $INSTALL_DIR"
  rm -rf "$INSTALL_DIR"
fi

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$CONFIG_DIR"

# 2) Copy application files
echo "📁 Copying application files..."
cp "$SCRIPT_DIR/onigiri.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/onigiri_ui.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/models.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/service.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/layout_canvas.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/onigiri_icon.png" "$INSTALL_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/README.md" "$INSTALL_DIR/" 2>/dev/null || true

# 3) Install default config ONLY if none exists
if [ ! -f "$CONFIG_DIR/onigiri.json" ]; then
  echo "📝 Installing default config to $CONFIG_DIR/onigiri.json"
  cp "$SCRIPT_DIR/onigiri.json" "$CONFIG_DIR/"
else
  echo "⚠️  Existing config at $CONFIG_DIR/onigiri.json detected – keeping it."
fi

# 4) Create virtual environment
echo "🐍 Creating virtual environment at $VENV_DIR..."
python3 -m venv "$VENV_DIR"

# 5) Install dependencies into venv
echo "📦 Installing Python dependencies into venv..."
"$VENV_DIR/bin/pip" install --upgrade pip

# IMPORTANT: look for requirements.txt in the SOURCE dir, not INSTALL dir
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
  echo "📄 Using requirements.txt from $SCRIPT_DIR"
  "$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
else
  echo "⚠️  requirements.txt not found in $SCRIPT_DIR – installing PyQt6 directly."
  "$VENV_DIR/bin/pip" install PyQt6
fi

# 6) Create launcher script in ~/.local/bin
LAUNCHER="$BIN_DIR/$APP_NAME"
echo "🚀 Creating launcher at $LAUNCHER"

cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Auto-generated launcher for $APP_NAME
VENV="$VENV_DIR"
APP_DIR="$INSTALL_DIR"

exec "\$VENV/bin/python" "\$APP_DIR/onigiri_ui.py" "\$@"
EOF

chmod +x "$LAUNCHER"

# 7) Create .desktop file for KDE / desktops
DESKTOP_FILE="$DESKTOP_DIR/onigiri.desktop"
echo "🖥  Creating desktop entry at $DESKTOP_FILE"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Onigiri
GenericName=KWin Rice Helper
Comment=Define and launch KWin tiling profiles
Exec=$APP_NAME
Icon=onigiri
Terminal=false
Categories=Utility;System;
StartupNotify=false
EOF

chmod +x "$DESKTOP_FILE"

echo
echo "✅ Installation complete."
echo
echo "➡  You can now run it via:"
echo "   $APP_NAME"
echo "   (or find 'Onigiri' in your application launcher)"
