#!/bin/bash
# 卸载 ClipGo 本地助手 (macOS)。不会删除已下载的视频。
set -u
SUPPORT="$HOME/Library/Application Support"
for dir in "Google/Chrome" "Google/Chrome Beta" "Google/Chrome Canary" "Chromium" \
           "Microsoft Edge" "Microsoft Edge Beta" "Microsoft Edge Dev" "BraveSoftware/Brave-Browser"; do
  rm -f "$SUPPORT/$dir/NativeMessagingHosts/com.clipgo.host.json"
done
rm -rf "$SUPPORT/ClipGo"
echo "✅ 已卸载 ClipGo 助手"
