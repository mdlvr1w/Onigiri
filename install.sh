#!/usr/bin/env bash
set -euo pipefail

INSTALL_HUE_SYNC=0
INSTALL_EXTENSIONS=0
for option in "$@"; do
  case "${option}" in
    --hue-sync) INSTALL_HUE_SYNC=1; INSTALL_EXTENSIONS=1 ;;
    --extensions) INSTALL_EXTENSIONS=1 ;;
    --help|-h) echo "Usage: $0 [--extensions] [--hue-sync]"; exit 0 ;;
    *) echo "Unknown option: ${option}" >&2; exit 1 ;;
  esac
done

APP_NAME="onigiri2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${HOME}/.local/share/${APP_NAME}"
VENV_DIR="${INSTALL_DIR}/venv"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/512x512/apps"

echo "Installing ${APP_NAME}"
echo "  Source:  ${SCRIPT_DIR}"
echo "  Install: ${INSTALL_DIR}"

# Keep the v2 environment (including optional dependencies) across upgrades.
# Replace only application code and assets; never reuse the archived v1 install.
rm -rf "${INSTALL_DIR}/onigiri" "${INSTALL_DIR}/assets"
mkdir -p "${INSTALL_DIR}" "${BIN_DIR}" "${DESKTOP_DIR}" "${ICON_DIR}"

cp -a "${SCRIPT_DIR}/onigiri" "${INSTALL_DIR}/onigiri"
cp -a "${SCRIPT_DIR}/assets" "${INSTALL_DIR}/assets"
cp "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/requirements.txt"
cp "${SCRIPT_DIR}/requirements-hue-sync.txt" "${INSTALL_DIR}/requirements-hue-sync.txt"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  python3 -m venv "${VENV_DIR}"
fi
"${VENV_DIR}/bin/python" -m pip install -r "${SCRIPT_DIR}/requirements.txt"

if [[ "${INSTALL_HUE_SYNC}" == 1 ]]; then
  "${VENV_DIR}/bin/python" -m pip install -r "${SCRIPT_DIR}/requirements-hue-sync.txt"
fi

if [[ "${INSTALL_EXTENSIONS}" == 1 ]]; then
  EXTENSIONS_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/onigiri/extensions"
  BACKUP_DIR="${EXTENSIONS_DIR}/.backups/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "${EXTENSIONS_DIR}"
  for source_path in "${SCRIPT_DIR}/examples/extensions/"*.py; do
    filename="${source_path##*/}"
    target_path="${EXTENSIONS_DIR}/${filename}"
    case "${filename}" in
      hue_sync.py|_hue_sync.py|_hue_sync_worker.py)
        if [[ "${INSTALL_HUE_SYNC}" == 0 && ! -f "${target_path}" ]]; then
          continue
        fi ;;
    esac
    if [[ -f "${target_path}" ]] && ! cmp -s "${source_path}" "${target_path}"; then
      mkdir -p "${BACKUP_DIR}"
      cp -a "${target_path}" "${BACKUP_DIR}/${filename}"
    fi
    cp "${source_path}" "${target_path}"
  done
  echo "Bundled extension controls installed. Hardware changes require Apply or Start."
fi

write_launcher() {
  local path="$1"
  cat > "${path}" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="${INSTALL_DIR}"
exec "${VENV_DIR}/bin/python" -m onigiri "\$@"
EOF
  chmod +x "${path}"
}

LAUNCHER="${BIN_DIR}/${APP_NAME}"
write_launcher "${LAUNCHER}"
write_launcher "${BIN_DIR}/onigiri"

cp "${SCRIPT_DIR}/assets/onigiri_icon.png" "${ICON_DIR}/onigiri.png"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" || true
fi

cat > "${DESKTOP_DIR}/onigiri.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Onigiri
GenericName=KWin layout helper
Comment=Split a monitor into tiles and place windows with KWin
Exec=${LAUNCHER}
Icon=onigiri
Terminal=false
Categories=Utility;System;
StartupNotify=false
EOF

echo
echo "Installed. Run: ${APP_NAME}"
