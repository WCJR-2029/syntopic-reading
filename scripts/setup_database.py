"""Initialize the syntopic reading database"""

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
