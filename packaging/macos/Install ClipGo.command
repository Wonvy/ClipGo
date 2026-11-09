#!/bin/bash
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$HOME/Library/Application Support/ClipGo"
finish() {
  result=$?
  if [[ $result -ne 0 ]]; then
    printf '\nInstallation did not finish. See the message above.\n安装尚未完成，请查看上方提示。\n'
  fi
  printf '\nPress Enter to close / 按回车关闭… '
  read -r reply || true
}
trap finish EXIT
if [[ "$(uname -s)" != 'Darwin' ]]; then
  echo 'This installer is for macOS only.'
  exit 1
fi
bash "$SOURCE/host/install-mac.sh"
# Load the extension from a stable location, not from Downloads or a mounted disk.
mkdir -p "$APP_DIR/extension"
/usr/bin/ditto "$SOURCE/extension" "$APP_DIR/extension"
printf '\n扩展文件夹 / Extension folder:\n%s\n' "$APP_DIR/extension"
printf '\nChrome: chrome://extensions\nEdge:   edge://extensions\n'
printf '\n打开扩展页 → 开启开发者模式 → 加载已解压的扩展程序。\n'
printf '选择文件夹时按 Command+Shift+G，输入上方路径。\n'
printf 'Open extensions → Developer mode → Load unpacked.\n'
printf 'In the folder picker press Command+Shift+G and enter the path above.\n'
printf 'Already loaded? Reload ClipGo and refresh your video pages.\n'
/usr/bin/open "$APP_DIR/extension"
