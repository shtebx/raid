"""Fix broken WireGuard client Address on server; install Xray VMess+WS; save links locally."""
import base64
import json
import secrets
import sys
import uuid

import paramiko

HOST = "144.31.201.14"
USER = "root"
PASSWORDS = [
    "miviZmNEzbaTzDl5WkVEkl7G",
    "miviZmNEzbaTzDl5WkVKvoQ1hRMEkI7G",
]
XRAY_PORT = 20950
WS_PATH = "/vmessws"
OUT_DIR = r"c:\Users\92731\Desktop\anti-raid"


def ssh_connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    for pw in PASSWORDS:
        try:
            c.connect(
                HOST,
                username=USER,
                password=pw,
                timeout=30,
                allow_agent=False,
                look_for_keys=False,
            )
            return c
        except Exception:
            continue
    raise SystemExit("SSH: wrong password or unreachable")


def run(c, cmd, timeout=300):
    _, stdout, stderr = c.exec_command(cmd, get_pty=True, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    c = ssh_connect()
    code, out, err = run(
        c,
        "cat /root/wg0-client-user1.conf 2>/dev/null; echo ---; wg show wg0 2>/dev/null | head -5",
        timeout=60,
    )
    print("=== current client conf ===\n", out[:2500])

    # Fix broken Address lines (missing client host id)
    fix_sh = r"""
set -e
F=/root/wg0-client-user1.conf
if [ ! -f "$F" ]; then echo "no client conf"; exit 1; fi
# normalize broken angristan output
sed -i 's|^Address = 10\.66\.66\./32|Address = 10.66.66.2/32|' "$F"
sed -i 's|^Address = 10\.66\.66\.\([0-9]*\)/32,fd42:42:42::/128|Address = 10.66.66.\1/32,fd42:42:42::2/128|' "$F"
sed -i 's|,fd42:42:42::/128|,fd42:42:42::2/128|g' "$F"
grep -q '10.66.66.2/32' "$F" || { echo "fix failed"; cat "$F"; exit 1; }
grep -q 'fd42:42:42::2/128' "$F" || sed -i 's|fd42:42:42::/128|fd42:42:42::2/128|' "$F"
systemctl restart wg-quick@wg0 || true
echo OK_CLIENT_FIX
"""
    code, out, err = run(c, fix_sh, timeout=120)
    print(out)
    if err.strip():
        print("STDERR", err, file=sys.stderr)
    if code != 0:
        c.close()
        sys.exit(code)

    uid = str(uuid.uuid4())
    xray_config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "0.0.0.0",
                "port": XRAY_PORT,
                "protocol": "vmess",
                "settings": {
                    "clients": [{"id": uid, "alterId": 0}],
                },
                "streamSettings": {
                    "network": "ws",
                    "wsSettings": {"path": WS_PATH},
                },
            }
        ],
        "outbounds": [{"protocol": "freedom", "tag": "direct"}],
    }
    cfg_json = json.dumps(xray_config, separators=(",", ":"))

    install = f"""
set -e
export DEBIAN_FRONTEND=noninteractive
ARCH=$(uname -m)
case "$ARCH" in x86_64) ARCH=64;; aarch64) ARCH=arm64-v8a;; *) echo "unsupported arch $ARCH"; exit 1;; esac
VER=$(curl -fsSL https://api.github.com/repos/XTLS/Xray-core/releases/latest | grep tag_name | head -1 | cut -d '"' -f4)
curl -fsSL -o /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/download/${{VER}}/Xray-linux-${{ARCH}}.zip"
apt-get update -qq && apt-get install -y -qq unzip
mkdir -p /usr/local/etc/xray
unzip -o /tmp/xray.zip -d /usr/local/bin xray geoip.dat geosite.dat
chmod +x /usr/local/bin/xray
cat > /usr/local/etc/xray/config.json <<'JSONCFG'
{cfg_json}
JSONCFG
cat > /etc/systemd/system/xray.service <<'UNIT'
[Unit]
Description=Xray Service
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable xray
systemctl restart xray
sleep 1
systemctl is-active xray
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q 'Status: active'; then
  ufw allow {XRAY_PORT}/tcp comment xray-vmess || true
fi
echo OK_XRAY
"""
    code, out, err = run(c, install, timeout=400)
    print(out[-2000:])
    if code != 0:
        print(err, file=sys.stderr)
        c.close()
        sys.exit(code)

    vmess_obj = {
        "v": "2",
        "ps": "vds-vmess",
        "add": HOST,
        "port": str(XRAY_PORT),
        "id": uid,
        "aid": "0",
        "scy": "auto",
        "net": "ws",
        "type": "none",
        "host": "",
        "path": WS_PATH,
        "tls": "",
    }
    vmess_link = "vmess://" + base64.b64encode(
        json.dumps(vmess_obj, separators=(",", ":")).encode()
    ).decode()

    # v2raytun / happ often accept subscription: base64(one vmess per line)
    sub_b64 = base64.b64encode((vmess_link + "\n").encode()).decode()

    open(OUT_DIR + "/vmess-link.txt", "w", encoding="utf-8").write(
        vmess_link + "\n\nSubscription (paste in Happ as subscription URL):\n"
        "data:text/plain;base64," + sub_b64 + "\n\n"
        "Or raw base64 for clipboard:\n" + sub_b64 + "\n"
    )

    sftp = c.open_sftp()
    with sftp.file("/tmp/vmess_uri.txt", "w") as rf:
        rf.write(vmess_link)
    sftp.close()

    run(
        c,
        "command -v qrencode >/dev/null && qrencode -t PNG -o /tmp/vmess.png < /tmp/vmess_uri.txt 2>/dev/null || true",
        timeout=30,
    )
    try:
        c.open_sftp().get("/tmp/vmess.png", OUT_DIR + "/vmess-qr.png")
    except Exception:
        pass

    # refresh WG qr on server + download
    run(
        c,
        "qrencode -t PNG -o /tmp/wgfix.png < /root/wg0-client-user1.conf 2>/dev/null || true",
        timeout=30,
    )
    try:
        c.open_sftp().get("/tmp/wgfix.png", OUT_DIR + "/wg0-client-user1-qr-fixed.png")
    except Exception:
        pass
    try:
        c.open_sftp().get("/root/wg0-client-user1.conf", OUT_DIR + "/wg0-client-user1.conf")
    except Exception:
        pass

    try:
        import qrcode

        qrcode.make(vmess_link).save(OUT_DIR + "/vmess-qr.png")
    except Exception:
        pass

    c.close()
    print("\nSaved:", OUT_DIR + "/vmess-link.txt", OUT_DIR + "/vmess-qr.png (if qrencode)")
    print("UUID:", uid)


if __name__ == "__main__":
    main()
