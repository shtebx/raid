"""
Одноразовый деплой на VDS: архив проекта + команды на сервере.
Пароль: переменная окружения VDS_PASSWORD или аргумент (не коммитьте пароль в репо).
"""
from __future__ import annotations

import io
import os
import sys
import tarfile
from pathlib import Path

import paramiko

HOST = "144.31.201.14"
USER = "root"
REMOTE_TAR = "/tmp/anti-raid-deploy.tar.gz"
INSTALL_DIR = "/opt/anti-raid"


def tar_filter(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
    p = ti.name.replace("\\", "/")
    if "/.venv/" in p or p.endswith("/.venv"):
        return None
    if "__pycache__" in p:
        return None
    if "/.git/" in p or p.endswith("/.git"):
        return None
    return ti


def _safe_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main() -> None:
    _safe_streams()
    password = os.environ.get("VDS_PASSWORD") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not password:
        print("Укажите VDS_PASSWORD в окружении или передайте пароль первым аргументом.", file=sys.stderr)
        sys.exit(1)

    root = Path(__file__).resolve().parent.parent
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(root, arcname="anti-raid", filter=tar_filter)
    buf.seek(0)
    data = buf.read()

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=password, timeout=45, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.file(REMOTE_TAR, "wb") as rf:
        rf.write(data)
    sftp.close()

    install_script = r"""set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y python3-venv nginx openssl

rm -rf /opt/anti-raid
mkdir -p /opt
tar xzf /tmp/anti-raid-deploy.tar.gz -C /opt

python3 -m venv /opt/anti-raid/.venv
/opt/anti-raid/.venv/bin/pip install -q -U pip
/opt/anti-raid/.venv/bin/pip install -q -r /opt/anti-raid/requirements.txt

if [ ! -f /etc/anti-raid.env ]; then
  echo "SECRET_KEY=$(openssl rand -hex 32)" > /etc/anti-raid.env
  chmod 600 /etc/anti-raid.env
fi

install -m 644 /opt/anti-raid/deploy/anti-raid.service /etc/systemd/system/anti-raid.service
install -m 644 /opt/anti-raid/deploy/nginx-antiraid.conf /etc/nginx/sites-available/anti-raid
ln -sf /etc/nginx/sites-available/anti-raid /etc/nginx/sites-enabled/anti-raid
rm -f /etc/nginx/sites-enabled/default

chown -R www-data:www-data /opt/anti-raid

systemctl daemon-reload
systemctl enable anti-raid
systemctl restart anti-raid
nginx -t
systemctl reload nginx || systemctl restart nginx

systemctl is-active anti-raid && systemctl is-active nginx && echo OK
"""

    stdin, stdout, stderr = client.exec_command(install_script, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    client.close()

    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    if code != 0:
        sys.exit(code)
    print(f"\nГотово: http://{HOST}/  админка: http://{HOST}/admin")


if __name__ == "__main__":
    main()
