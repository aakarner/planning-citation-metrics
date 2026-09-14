"""Planning Citation Metrics pipeline.

Modules:
  load_workbook  one-time migration of the handover workbook into roster CSVs
                 and a first metric snapshot
  build_db       CSV + snapshots -> SQLite with derived views
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
ROSTER_DIR = DATA_DIR / "roster"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
LEGACY_DIR = DATA_DIR / "legacy"
PRIVATE_DIR = DATA_DIR / "private"      # gitignored: gender and anything else not for publication
SQL_DIR = REPO_ROOT / "sql"
BUILD_DIR = REPO_ROOT / "build"

RANKS = ("assistant", "associate", "full", "other")
