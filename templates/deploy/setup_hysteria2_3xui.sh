#!/usr/bin/env bash
# One-shot: Hysteria2 + 3x-ui + UFW + ip_forward + VLESS/XHTTP inbound (API).
# Run as root on Ubuntu/Debian (22.04+). Requires outbound HTTPS.
#
# Env (optional):
#   PUBLIC_IP          — override detected IPv4
#   HY2_UDP_PORT       — default 8443
#   VLESS_TCP_PORT     — default 20952
#   XHTTP_PATH         — default /xhttp-vless
#   SKIP_3XUI          — 1 = do not reinstall 3x-ui panel
#   SKIP_HYSTERIA      — 1 = skip Hysteria install/config

set -euo pipefail

[[ "${EUID:-}" -eq 0 ]] || { echo "Run as root." >&2; exit 1; }

HY2_UDP_PORT="${HY2_UDP_PORT:-8443}"
VLESS_TCP_PORT="${VLESS_TCP_PORT:-20952}"
XHTTP_PATH="${XHTTP_PATH:-/xhttp-vless}"

XUI_BIN="${XUI_BIN:-/usr/local/x-ui/x-ui}"
XUI_DB="${XUI_DB:-/etc/x-ui/x-ui.db}"
HYSTERIA_CFG="${HYSTERIA_CFG:-/etc/hysteria/config.yaml}"

log() { echo "[setup] $*"; }

rand_alnum() { openssl rand -base64 32 | tr -dc 'a-zA-Z0-9' | head -c "$1"; }

detect_ip() {
  if [[ -n "${PUBLIC_IP:-}" ]]; then
    echo "$PUBLIC_IP"
    return
  fi
  local ip=""
  for u in "https://api4.ipify.org" "https://ipv4.icanhazip.com"; do
    ip="$(curl -4fsS --max-time 5 "$u" 2>/dev/null | tr -d '[:space:]')" || true
    if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      echo "$ip"
      return
    fi
  done
  echo ""
}

PUBLIC_IP="$(detect_ip)"
[[ -n "$PUBLIC_IP" ]] || { echo "Could not detect PUBLIC_IP; set PUBLIC_IP=1.2.3.4" >&2; exit 1; }

log "Public IPv4: $PUBLIC_IP"

# --- sysctl (idempotent) ---
mkdir -p /etc/sysctl.d
cat >/etc/sysctl.d/99-vpn-forward.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
sysctl -p /etc/sysctl.d/99-vpn-forward.conf >/dev/null 2>&1 || sysctl --system >/dev/null 2>&1 || true

# --- packages ---
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates openssl jq sqlite3 qrencode ufw >/dev/null

# --- UFW baseline (panel port added later) ---
ufw allow OpenSSH >/dev/null 2>&1 || ufw allow 22/tcp >/dev/null 2>&1
ufw allow "${HY2_UDP_PORT}/udp" >/dev/null 2>&1
ufw allow "${VLESS_TCP_PORT}/tcp" >/dev/null 2>&1
ufw --force enable >/dev/null
ufw reload >/dev/null

# --- Self-signed cert for 3x-ui (custom SSL path #3, no port 80 needed) ---
mkdir -p /root/cert/panel
if [[ ! -s /root/cert/panel/fullchain.pem || ! -s /root/cert/panel/key.pem ]]; then
  if ! openssl req -x509 -nodes -newkey rsa:2048 -days 1825 \
    -keyout /root/cert/panel/key.pem \
    -out /root/cert/panel/fullchain.pem \
    -subj "/CN=${PUBLIC_IP}" \
    -addext "subjectAltName=IP:${PUBLIC_IP}" 2>/dev/null; then
    openssl req -x509 -nodes -newkey rsa:2048 -days 1825 \
      -keyout /root/cert/panel/key.pem \
      -out /root/cert/panel/fullchain.pem \
      -subj "/CN=${PUBLIC_IP}" >/dev/null 2>&1
  fi
  chmod 600 /root/cert/panel/key.pem
  chmod 644 /root/cert/panel/fullchain.pem
fi

# --- 3x-ui ---
if [[ "${SKIP_3XUI:-0}" != "1" ]]; then
  log "Installing / updating 3x-ui (non-interactive: random panel port + custom SSL)..."
  curl -fsSL https://raw.githubusercontent.com/MHSanaei/3x-ui/master/install.sh -o /tmp/3x-ui-install.sh
  chmod +x /tmp/3x-ui-install.sh
  # Answers: n = random panel port; 3 = custom cert; IP; cert; key
  /tmp/3x-ui-install.sh <<EOF
n
3
${PUBLIC_IP}
/root/cert/panel/fullchain.pem
/root/cert/panel/key.pem
EOF
  rm -f /tmp/3x-ui-install.sh
else
  log "SKIP_3XUI=1 — leaving existing 3x-ui as is."
fi

[[ -x "$XUI_BIN" ]] || { echo "x-ui binary missing at $XUI_BIN" >&2; exit 1; }

# Known panel credentials (CLI reset; does not need old password)
PANEL_USER="${PANEL_USER:-admin}"
PANEL_PASS="${PANEL_PASS:-$(rand_alnum 20)}"
"$XUI_BIN" setting -username "$PANEL_USER" -password "$PANEL_PASS" >/dev/null
systemctl restart x-ui >/dev/null 2>&1 || true
sleep 2

PANEL_PORT="$("$XUI_BIN" setting -show true 2>/dev/null | awk -F': ' '/^port:/{gsub(/ /,"",$2); print $2; exit}')"
WEB_BASE_RAW="$("$XUI_BIN" setting -show true 2>/dev/null | awk -F': ' '/^webBasePath:/{print $2; exit}' | tr -d '[:space:]')"
[[ -n "$PANEL_PORT" ]] || { echo "Could not read panel port" >&2; exit 1; }
# URL path: ensure single slashes
WEB_BASE="${WEB_BASE_RAW#/}"
[[ -n "$WEB_BASE" ]] && WEB_BASE="${WEB_BASE%/}/" || WEB_BASE=""
PANEL_URL="https://${PUBLIC_IP}:${PANEL_PORT}/${WEB_BASE}"

ufw allow "${PANEL_PORT}/tcp" >/dev/null 2>&1
ufw reload >/dev/null

# --- API token in DB (Bearer bypasses CSRF) ---
API_TOKEN="$(rand_alnum 48)"
python3 <<PY
import sqlite3
conn = sqlite3.connect("${XUI_DB}")
c = conn.cursor()
c.execute("UPDATE settings SET value=? WHERE key=?", ("${API_TOKEN}", "apiToken"))
if c.rowcount == 0:
    c.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("apiToken", "${API_TOKEN}"))
conn.commit()
conn.close()
PY
systemctl restart x-ui >/dev/null 2>&1 || true
sleep 2

# --- Hysteria2 ---
HY2_PASS="${HY2_PASS:-$(rand_alnum 24)}"
OBFS_PASS="${OBFS_PASS:-$(rand_alnum 16)}"
mkdir -p /etc/hysteria
if [[ ! -s /etc/hysteria/server.crt || ! -s /etc/hysteria/server.key ]]; then
  if ! openssl req -x509 -nodes -newkey rsa:2048 -days 1825 \
    -keyout /etc/hysteria/server.key \
    -out /etc/hysteria/server.crt \
    -subj "/CN=${PUBLIC_IP}" \
    -addext "subjectAltName=IP:${PUBLIC_IP}" 2>/dev/null; then
    openssl req -x509 -nodes -newkey rsa:2048 -days 1825 \
      -keyout /etc/hysteria/server.key \
      -out /etc/hysteria/server.crt \
      -subj "/CN=${PUBLIC_IP}" >/dev/null 2>&1
  fi
  chmod 600 /etc/hysteria/server.key
fi

if [[ "${SKIP_HYSTERIA:-0}" != "1" ]]; then
  HYSTERIA_USER=root bash <(curl -fsSL https://get.hy2.sh/) >/dev/null
fi

cat >"$HYSTERIA_CFG" <<EOF
listen: :${HY2_UDP_PORT}

tls:
  cert: /etc/hysteria/server.crt
  key: /etc/hysteria/server.key

auth:
  type: password
  password: "${HY2_PASS}"

obfs:
  type: salamander
  salamander:
    password: "${OBFS_PASS}"

masquerade:
  type: proxy
  proxy:
    url: https://www.bing.com
    rewriteHost: true

quic:
  initStreamReceiveWindow: 8388608
  maxStreamReceiveWindow: 8388608
  initConnReceiveWindow: 20971520
  maxConnReceiveWindow: 20971520
EOF

systemctl enable hysteria-server.service >/dev/null 2>&1 || true
systemctl restart hysteria-server.service >/dev/null 2>&1 || true
sleep 1

# --- VLESS + XHTTP via 3x-ui API ---
CLIENT_UUID="$(cat /proc/sys/kernel/random/uuid)"
VLESS_EMAIL="vless-xhttp"

API_LIST="${PANEL_URL}panel/api/inbounds/list"
LIST_JSON="$(curl -sk -H "Authorization: Bearer ${API_TOKEN}" "$API_LIST")"
if echo "$LIST_JSON" | jq -e --argjson p "$VLESS_TCP_PORT" '(.obj // []) | map(select(.port == $p)) | length > 0' >/dev/null 2>&1; then
  log "Inbound on port ${VLESS_TCP_PORT} already exists — skipping API add."
  ADD_RESP='{"success":true,"msg":"skipped"}'
  CLIENT_UUID="$(echo "$LIST_JSON" | jq -r --argjson p "$VLESS_TCP_PORT" '
    (.obj // []) | map(select(.port == $p)) | .[0].settings // ""
    | (try (fromjson | .clients[0].id) catch "")
  ')"
  [[ -n "$CLIENT_UUID" && "$CLIENT_UUID" != "null" ]] || CLIENT_UUID="(open panel → Inbounds)"
else
SETTINGS_INNER="$(jq -nc --arg id "$CLIENT_UUID" --arg em "$VLESS_EMAIL" \
  '{clients:[{id:$id,email:$em,flow:"",encryption:"none"}],decryption:"none",fallbacks:[]}')"
STREAM_INNER="$(jq -nc --arg p "$XHTTP_PATH" \
  '{network:"xhttp",security:"none",xhttpSettings:{path:$p,mode:"auto"}}')"
SNIFF_INNER="$(jq -nc '{enabled:true,destOverride:["http","tls","quic","fakedns"]}')"
INBOUND_JSON="$(jq -nc \
  --arg remark "VLESS+XHTTP (auto)" \
  --argjson port "$VLESS_TCP_PORT" \
  --arg settings "$SETTINGS_INNER" \
  --arg stream "$STREAM_INNER" \
  --arg sniff "$SNIFF_INNER" \
  '{remark:$remark,listen:"",port:$port,protocol:"vless",settings:$settings,streamSettings:$stream,sniffing:$sniff,enable:true}')"

API_ADD="${PANEL_URL}panel/api/inbounds/add"
ADD_RESP="$(curl -sk -X POST "$API_ADD" \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$INBOUND_JSON")" || true
fi

if echo "$ADD_RESP" | jq -e '(.success == true) or (.success == "true")' >/dev/null 2>&1; then
  if echo "$ADD_RESP" | jq -e '.msg == "skipped"' >/dev/null 2>&1; then
    log "Inbound on port ${VLESS_TCP_PORT} already present."
  else
    log "Inbound VLESS+XHTTP created on port ${VLESS_TCP_PORT}."
    systemctl restart x-ui >/dev/null 2>&1 || true
  fi
else
  log "WARN: API add inbound failed (create manually in panel). Response:"
  echo "$ADD_RESP" | head -c 800 || true
fi

# --- hy2 URI (percent-encode secrets) ---
export _HY2P="$HY2_PASS" _HY2O="$OBFS_PASS"
ENC_PASS="$(python3 -c 'import os,urllib.parse; print(urllib.parse.quote(os.environ["_HY2P"], safe=""))')"
ENC_OBFS="$(python3 -c 'import os,urllib.parse; print(urllib.parse.quote(os.environ["_HY2O"], safe=""))')"
unset _HY2P _HY2O
HY2_URI="hy2://${ENC_PASS}@${PUBLIC_IP}:${HY2_UDP_PORT}/?insecure=1&obfs=salamander&obfs-password=${ENC_OBFS}"

echo ""
echo "============================ SUMMARY ============================"
echo "Server IP:              ${PUBLIC_IP}"
echo "3x-ui panel URL:        ${PANEL_URL}"
echo "3x-ui login:            ${PANEL_USER}"
echo "3x-ui password:         ${PANEL_PASS}"
echo "3x-ui API Bearer:       ${API_TOKEN}"
echo ""
echo "Hysteria2 (UDP ${HY2_UDP_PORT}, Salamander obfs):"
echo "  hy2 URI:"
echo "  ${HY2_URI}"
echo ""
echo "Hysteria2 QR (terminal):"
if command -v qrencode >/dev/null 2>&1; then
  qrencode -t ANSIUTF8 <<<"$HY2_URI"
else
  echo "  (qrencode not available)"
fi
echo ""
echo "VLESS+XHTTP (backup, TCP ${VLESS_TCP_PORT}):"
echo "  UUID:   ${CLIENT_UUID}"
echo "  path:   ${XHTTP_PATH}"
echo "  Import from 3x-ui → Inbounds, or build vless:// in your client."
echo ""
echo "v2rayTun: supports VLESS; for Hysteria 2 use Sing-box / dedicated"
echo "Hysteria profile if the build you use does not list hy2 natively."
echo "==================================================================="
