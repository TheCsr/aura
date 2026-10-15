"""Launch the resident device using repository .env host/port settings."""

import os
from pathlib import Path
import subprocess
import sys


_PROJECT_ROOT = Path(__file__).resolve().parent


def _load_env() -> None:
    env_file = _PROJECT_ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


def main() -> int:
    _load_env()
    host = os.environ.get("AURA_DEVICE_HOST", "127.0.0.1")
    port = os.environ.get("AURA_DEVICE_PORT", "8501")
    try:
        port_number = int(port)
    except ValueError as error:
        raise ValueError("AURA_DEVICE_PORT must be an integer") from error
    if not 1 <= port_number <= 65535:
        raise ValueError("AURA_DEVICE_PORT must be between 1 and 65535")

    return subprocess.call([
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(_PROJECT_ROOT / "resident_device_app.py"),
        "--server.address",
        host,
        "--server.port",
        str(port_number),
    ], cwd=_PROJECT_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
