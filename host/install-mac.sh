#!/bin/bash
# ClipGo 本地助手安装脚本 (macOS)
# 用法: bash install-mac.sh          安装 / 更新
#       bash install-mac.sh --force  强制重新下载 ffmpeg 和 deno
set -euo pipefail

EXT_ID="mnhnajpalaecohikjibocdeamfmnpena"
HOST_NAME="com.clipgo.host"
APP_DIR="$HOME/Library/Application Support/ClipGo"
BIN="$APP_DIR/bin"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
FORCE="${1:-}"

case "$(uname -m)" in
  arm64)  FF_ARCH="arm64"; DENO_ARCH="aarch64" ;;
  x86_64) FF_ARCH="amd64"; DENO_ARCH="x86_64" ;;
  *) echo "不支持的 CPU 架构: $(uname -m)"; exit 1 ;;
esac

if ! /usr/bin/python3 -c "import sys; assert sys.version_info >= (3, 8)" 2>/dev/null; then
  echo "需要 Python 3（系统自带）。请先运行: xcode-select --install"
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$BIN"

fetch() { echo "↓ $2"; curl -fL --retry 3 --progress-bar -o "$1" "$2"; }

echo "==> 安装 ClipGo 助手到: $APP_DIR"
cp "$SRC_DIR/clipgo_host.py" "$APP_DIR/clipgo_host.py"
chmod +x "$APP_DIR/clipgo_host.py"

echo "==> yt-dlp（每次安装都会更新到最新版）"
# 用解压版（onedir）而不是单文件版：单文件版每次运行都会把 Python.framework
# 解压到临时目录，被浏览器启动时这些文件会被加上隔离标记，弹出「已损坏」提示。
fetch "$TMP/yt-dlp.zip" "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos.zip"
ditto -x -k "$TMP/yt-dlp.zip" "$TMP/yt-dlp-mac"
rm -rf "$BIN/yt-dlp-mac" "$BIN/yt-dlp"
mv "$TMP/yt-dlp-mac" "$BIN/yt-dlp-mac"

if [[ "$FORCE" == "--force" || ! -x "$BIN/ffmpeg" || ! -x "$BIN/ffprobe" ]]; then
  echo "==> ffmpeg / ffprobe ($FF_ARCH)"
  for tool in ffmpeg ffprobe; do
    fetch "$TMP/$tool.zip" "https://ffmpeg.martin-riedl.de/redirect/latest/macos/$FF_ARCH/release/$tool.zip"
    unzip -o -q "$TMP/$tool.zip" -d "$TMP/$tool"
    find "$TMP/$tool" -type f -name "$tool" -exec mv {} "$BIN/$tool" \;
  done
else
  echo "==> ffmpeg 已存在，跳过"
fi

if [[ "$FORCE" == "--force" || ! -x "$BIN/deno" ]]; then
  echo "==> deno（YouTube 解密需要的 JS 运行时）"
  fetch "$TMP/deno.zip" "https://github.com/denoland/deno/releases/latest/download/deno-$DENO_ARCH-apple-darwin.zip"
  unzip -o -q "$TMP/deno.zip" -d "$BIN"
else
  echo "==> deno 已存在，跳过"
fi

chmod +x "$BIN"/ffmpeg "$BIN"/ffprobe "$BIN"/deno "$BIN"/yt-dlp-mac/yt-dlp_macos
xattr -dr com.apple.quarantine "$APP_DIR" 2>/dev/null || true

echo "==> 注册 Native Messaging"
MANIFEST_JSON=$(cat <<EOF
{
  "name": "$HOST_NAME",
  "description": "ClipGo download helper",
  "path": "$APP_DIR/clipgo_host.py",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://$EXT_ID/"]
}
EOF
)
SUPPORT="$HOME/Library/Application Support"
# Chrome 和 Edge 总是注册（之后再装浏览器也能直接用）；其他 Chromium 浏览器已安装才注册。
for dir in "Google/Chrome" "Microsoft Edge"; do
  mkdir -p "$SUPPORT/$dir/NativeMessagingHosts"
  echo "$MANIFEST_JSON" > "$SUPPORT/$dir/NativeMessagingHosts/$HOST_NAME.json"
  echo "   ✓ $dir"
done
for dir in "Google/Chrome Beta" "Google/Chrome Canary" "Chromium" "Microsoft Edge Beta" "Microsoft Edge Dev" "BraveSoftware/Brave-Browser"; do
  if [[ -d "$SUPPORT/$dir" ]]; then
    mkdir -p "$SUPPORT/$dir/NativeMessagingHosts"
    echo "$MANIFEST_JSON" > "$SUPPORT/$dir/NativeMessagingHosts/$HOST_NAME.json"
    echo "   ✓ $dir"
  fi
done

echo "==> 自检"
echo "   yt-dlp $("$BIN/yt-dlp-mac/yt-dlp_macos" --version)"
echo "   $("$BIN/ffmpeg" -version | head -1)"
echo "   $("$BIN/deno" --version | head -1)"
echo
echo "✅ 安装完成。在浏览器扩展页面重新加载 ClipGo 后即可使用。"
