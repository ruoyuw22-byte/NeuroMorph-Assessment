#!/bin/bash
set -u
APP_DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
SUPPORT="$HOME/Library/Application Support/BrainerLite"
mkdir -p "$SUPPORT/sessions" "$SUPPORT/native_shell"
LOG="$SUPPORT/launcher.log"
CONFIG="$SUPPORT/config.json"
VERSION="$(cat "$APP_DIR/NMA_VERSION.txt" 2>/dev/null || echo '0.10.0')"
export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/opt/anaconda3/bin:/opt/anaconda3/bin:$HOME/anaconda3/bin:$HOME/miniconda3/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
log(){ printf '%s\n' "$*" >> "$LOG"; }

config_python(){
  if [ -f "$CONFIG" ]; then
    /usr/bin/grep -o '\"python\"[[:space:]]*:[[:space:]]*\"[^\"]*\"' "$CONFIG" 2>/dev/null | /usr/bin/head -1 | /usr/bin/sed -E 's/^.*:[[:space:]]*\"(.*)\"$/\1/'
  fi
}
find_python(){
  CPY="$(config_python || true)"
  if [ -n "${CPY:-}" ] && [ -x "$CPY" ]; then echo "$CPY"; return 0; fi
  for p in "$HOME/opt/anaconda3/envs/brainer/bin/python" /opt/anaconda3/envs/brainer/bin/python "$HOME/anaconda3/envs/brainer/bin/python" "$HOME/miniconda3/envs/brainer/bin/python"; do
    if [ -x "$p" ]; then echo "$p"; return 0; fi
  done
  return 1
}
PY="$(find_python || true)"
if [ -z "$PY" ]; then
  /usr/bin/osascript -e 'display alert "NMA 无法启动" message "未找到 brainer Python 解释器。请在 NMA 设置中配置 Python，或创建名为 brainer 的 Conda 环境。" as critical'
  exit 1
fi

# Stop only legacy NMA backends that older versions left behind. Current NMA never reuses them.
OLD_SERVER="$SUPPORT/server.json"
if [ -f "$OLD_SERVER" ]; then
  OPID="$($PY -c 'import json,sys; j=json.load(open(sys.argv[1])); print(j.get("pid",""))' "$OLD_SERVER" 2>/dev/null || true)"
  if [[ "$OPID" =~ ^[0-9]+$ ]] && /bin/kill -0 "$OPID" 2>/dev/null; then /bin/kill "$OPID" 2>/dev/null || true; fi
  rm -f "$OLD_SERVER"
fi

SESSION_ID="$(date +%s)-$$-$RANDOM"
SESSION_DIR="$SUPPORT/sessions/$SESSION_ID"
mkdir -p "$SESSION_DIR"
SERVER_FILE="$SESSION_DIR/server.json"
BACKEND_LOG="$SESSION_DIR/backend.log"

{
  echo "==== $(date) ===="
  echo "NMA launcher $VERSION"
  echo "APP_DIR=$APP_DIR"
  echo "PY=$PY"
  "$PY" --version
  echo "SESSION=$SESSION_ID"
} >> "$LOG" 2>&1

# Compile a real native Cocoa/WKWebView host once for this version.
HELPER_DIR="$SUPPORT/native_shell/$VERSION"
HELPER="$HELPER_DIR/NMAWebView"
mkdir -p "$HELPER_DIR"
if [ ! -x "$HELPER" ]; then
  if [ ! -x /usr/bin/clang ]; then
    /usr/bin/osascript -e 'display alert "NMA 无法启动原生窗口" message "未找到 macOS clang。当前版本要求 Xcode Command Line Tools 来构建一次原生窗口组件。" as critical'
    exit 1
  fi
  log "Compiling native Cocoa/WKWebView host..."
  if ! /usr/bin/clang -fobjc-arc -framework Cocoa -framework WebKit -mmacosx-version-min=10.15 "$APP_DIR/NMAWebView.m" -o "$HELPER" >> "$LOG" 2>&1; then
    /usr/bin/osascript -e 'display alert "NMA 原生窗口构建失败" message "请把 ~/Library/Application Support/BrainerLite/launcher.log 发给开发者。" as critical'
    exit 1
  fi
  chmod +x "$HELPER"
fi

"$PY" "$APP_DIR/app.py" --no-browser --server-file "$SERVER_FILE" --token "$SESSION_ID" >> "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
log "Started isolated backend PID=$BACKEND_PID"

URL=""
for i in $(seq 1 600); do
  if ! /bin/kill -0 "$BACKEND_PID" 2>/dev/null; then
    log "ERROR: backend exited before ready."
    tail -n 100 "$BACKEND_LOG" >> "$LOG" 2>/dev/null || true
    /usr/bin/osascript -e 'display alert "NMA 后台启动失败" message "本地分析服务提前退出。请查看 ~/Library/Application Support/BrainerLite/launcher.log" as critical'
    exit 1
  fi
  if [ -f "$SERVER_FILE" ]; then
    READ="$($PY -c 'import json,sys; j=json.load(open(sys.argv[1])); print("%s|%s|%s"%(j.get("url",""),j.get("version",""),j.get("token","")))' "$SERVER_FILE" 2>/dev/null || true)"
    IFS='|' read -r CAND_URL CAND_VER CAND_TOKEN <<< "$READ"
    if [ "$CAND_VER" = "$VERSION" ] && [ "$CAND_TOKEN" = "$SESSION_ID" ] && [ -n "$CAND_URL" ]; then
      if /usr/bin/curl --noproxy '*' -fsS --connect-timeout 1 --max-time 2 "$CAND_URL/api/version" >/dev/null 2>&1; then URL="$CAND_URL"; break; fi
    fi
  fi
  sleep .1
done

if [ -z "$URL" ]; then
  log "ERROR: isolated backend did not become ready within 60 seconds"
  tail -n 100 "$BACKEND_LOG" >> "$LOG" 2>/dev/null || true
  /bin/kill "$BACKEND_PID" 2>/dev/null || true
  /usr/bin/osascript -e 'display alert "NMA 启动失败" message "本地分析服务未能在 60 秒内启动。请查看 launcher.log。" as critical'
  exit 1
fi

log "Native window URL=$URL PID=$BACKEND_PID VERSION=$VERSION"
# exec makes the Cocoa host become this .app's foreground process. No Chrome/Safari is opened.
exec "$HELPER" "$URL" "$APP_DIR/AppIcon.png" "$BACKEND_PID"
