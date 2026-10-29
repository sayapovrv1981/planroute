"""PostgreSQL для разработки и тестов без установки в систему и без Docker.

Программы PostgreSQL 16 берём из колеса pgserver с PyPI: его Python-часть нам не нужна (колёса есть только
под Python 3.12), а сами программы от версии Python не зависят. Колесо распаковываем в backend/var/pgbin.

- `manage.py devdb start|stop|status` — постоянная база разработки в backend/var/postgres, порт 54329;
- `temp_cluster()` — временная база для тестов: свой порт и папка, удаляется по выходе.
"""
from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
BIN_ROOT = BACKEND / "var" / "pgbin"
WHEEL = "pgserver==0.1.4"


def bin_dir() -> Path:
    """Папка с initdb, pg_ctl, psql; при первом вызове скачивает колесо (около 15 МБ)."""
    bindir = BIN_ROOT / "pgserver" / "pginstall" / "bin"
    if (bindir / "pg_ctl").is_file():
        return bindir
    with tempfile.TemporaryDirectory() as tmp:
        print("→ скачиваю программы PostgreSQL (колесо pgserver)", file=sys.stderr)
        subprocess.run([sys.executable, "-m", "pip", "download", "-q", "--no-deps", "--only-binary=:all:",
                        "--python-version", "3.12", "-d", tmp, WHEEL], check=True)
        wheel = next(Path(tmp).glob("pgserver-*.whl"))
        shutil.rmtree(BIN_ROOT, ignore_errors=True)
        with zipfile.ZipFile(wheel) as z:
            for info in z.infolist():
                if info.filename.startswith("pgserver/"):  # программы и библиотеки к ним (.dylibs, pginstall/lib)
                    z.extract(info, BIN_ROOT)
                    mode = info.external_attr >> 16
                    if mode:
                        os.chmod(BIN_ROOT / info.filename, mode)
    return bindir


def _run(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(bin_dir() / args[0]), *args[1:]], check=True, **kw)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def init(datadir: Path) -> None:
    if not (datadir / "PG_VERSION").is_file():
        datadir.mkdir(parents=True, exist_ok=True)
        _run("initdb", "-D", str(datadir), "-U", "postgres", "--auth=trust", "-E", "UTF8", "--locale=C",
             stdout=subprocess.DEVNULL)


def running(datadir: Path) -> bool:
    return subprocess.run([str(bin_dir() / "pg_ctl"), "-D", str(datadir), "status"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def start(datadir: Path, port: int) -> None:
    init(datadir)
    if not running(datadir):
        # сокет — в папке базы: не нужен доступ к /tmp и не мешаем чужому PostgreSQL
        _run("pg_ctl", "-D", str(datadir), "-l", str(datadir / "server.log"), "-w", "-s",
             "-o", f"-p {port} -h 127.0.0.1 -k {datadir}", "start")


def stop(datadir: Path) -> None:
    if (datadir / "PG_VERSION").is_file() and running(datadir):
        _run("pg_ctl", "-D", str(datadir), "-m", "fast", "-w", "-s", "stop")


def create_db(port: int, name: str) -> None:
    exists = subprocess.run([str(bin_dir() / "psql"), "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "-Atc",
                             f"select 1 from pg_database where datname = '{name}'"],
                            check=True, capture_output=True, text=True).stdout.strip()
    if not exists:
        _run("createdb", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", name)


def url(port: int, name: str) -> str:
    return f"postgresql+psycopg://postgres@127.0.0.1:{port}/{name}"


def temp_cluster() -> str:
    """Временный PostgreSQL на свободном порту; останавливается и удаляется при выходе из процесса."""
    datadir = Path(tempfile.mkdtemp(prefix="sh-pg-"))
    port = free_port()
    start(datadir, port)

    def cleanup() -> None:
        stop(datadir)
        shutil.rmtree(datadir, ignore_errors=True)

    atexit.register(cleanup)
    create_db(port, "tripplan")
    return url(port, "tripplan")
