"""Repository paths. Editable installs keep these pointed at the checkout."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config" / "laws.yaml"
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw"
DEFAULT_JSONL_DIR = REPO_ROOT / "data" / "processed"
DEFAULT_DATABASE_URL = "postgresql+psycopg://twbc:twbc@localhost:55432/twbc"
