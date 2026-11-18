"""Settings from .env (no external dependency)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> dict:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = val.strip()
    return values


_env = _load_env(ROOT / ".env")

HOST = _env.get("HOST", "127.0.0.1")
PORT = int(_env.get("PORT", "8765"))
DB_PATH = ROOT / _env.get("DB_PATH", "data/app.db")
BACKUP_DIR = ROOT / _env.get("BACKUP_DIR", "data/backup")
BACKUP_KEEP = int(_env.get("BACKUP_KEEP", "30"))
TEMPLATE_DIR = ROOT / "data" / "templates"
