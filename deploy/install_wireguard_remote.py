"""One-off: SSH to VDS and run angristan wireguard-install with defaults."""
import sys

import paramiko

HOST = "144.31.201.14"
USER = "root"
PASSWORDS = [
    "miviZmNEzbaTzDl5WkVEkl7G",
    "miviZmNEzbaTzDl5WkVKvoQ1hRMEkI7G",
]

REMOTE = r"""set -e
if [ -f /etc/wireguard/params ]; then
  echo "=== WireGuard already installed ==="
  systemctl is-active wg-quick@wg0 2>/dev/null || true
  wg show 2>/dev/null || true
  exit 0
fi
DEF_IF=$(ip -4 route show default 2>/dev/null | awk '/default/ {for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}' | head -1)
if [ -z "$DEF_IF" ]; then DEF_IF=eth0; fi
echo "Using public interface: $DEF_IF"
export DEBIAN_FRONTEND=noninteractive
cd /root
curl -fsSL -O https://raw.githubusercontent.com/angristan/wireguard-install/master/wireguard-install.sh
chmod +x wireguard-install.sh
./wireguard-install.sh <<ANS
144.31.201.14
$DEF_IF
wg0
10.66.66.1
fd42:42:42::1
51820
1.1.1.1
1.0.0.1
0.0.0.0/0,::/0
x
user1


ANS
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qE 'Status: active'; then
  ufw allow 51820/udp comment 'WireGuard' || true
  echo "UFW: allowed 51820/udp"
fi
echo "=== wg-quick status ==="
systemctl is-active wg-quick@wg0 || true
echo "=== Client config path ==="
ls -la /root/wg0-client-user1.conf 2>/dev/null || true
"""


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    last = None
    for pw in PASSWORDS:
        try:
            client.connect(
                HOST,
                username=USER,
                password=pw,
                timeout=45,
                allow_agent=False,
                look_for_keys=False,
            )
            last = None
            break
        except Exception as e:
            last = e
    else:
        print("SSH failed:", last, file=sys.stderr)
        sys.exit(1)

    stdin, stdout, stderr = client.exec_command(REMOTE, get_pty=True, timeout=600)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    client.close()
    sys.stdout.write(out)
    if err.strip():
        sys.stderr.write(err)
    sys.exit(code)


if __name__ == "__main__":
    main()
