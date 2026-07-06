"""Initialize the syntopic reading database"""

import sys as _sys, pathlib as _pl
_ROOT = str(_pl.Path(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)  # so `import src` works run-from-repo without `pip install -e .`

from src.database.schema import create_database
from src.config import DATABASE_PATH

if __name__ == "__main__":
    print("Initializing Syntopic Reading System database...")
    print(f"Database path: {DATABASE_PATH}")
    print()

    create_database(DATABASE_PATH)

    print("\n✅ Database setup complete!")
    print(f"\nYou can verify with:")
    print(f"  sqlite3 {DATABASE_PATH} '.tables'")
