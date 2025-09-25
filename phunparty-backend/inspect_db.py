"""
Quick database inspection script
Run this to check your database tables and the new photo column
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Load environment variables
env_path = Path(__file__).resolve().parents[1] / "credentials.env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

# Database connection
db_user = os.getenv("DB_User")
db_password = os.getenv("DB_Password")
db_host = os.getenv("DB_Host")
db_port = os.getenv("DB_Port")
db_name = os.getenv("DB_Name")

database_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
engine = create_engine(database_url)


def inspect_database():
    """Inspect database tables and structure"""
    with engine.connect() as conn:
        # List all tables
        result = conn.execute(
            text(
                """
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """
            )
        )

        print("📋 Database Tables:")
        tables = []
        for row in result:
            tables.append(row[0])
            print(f"  - {row[0]}")

        print("\n" + "=" * 50)

        # Check players table structure
        if "players" in tables:
            print("👤 Players Table Structure:")
            result = conn.execute(
                text(
                    """
                SELECT column_name, data_type, is_nullable 
                FROM information_schema.columns 
                WHERE table_name = 'players'
                ORDER BY ordinal_position
            """
                )
            )

            for row in result:
                nullable = "NULL" if row[2] == "YES" else "NOT NULL"
                print(f"  - {row[0]}: {row[1]} ({nullable})")

            print("\n📊 Sample Players Data:")
            result = conn.execute(
                text(
                    "SELECT player_id, player_name, player_email, profile_photo_url FROM players LIMIT 5"
                )
            )

            for row in result:
                photo_status = "📷 Has Photo" if row[3] else "❌ No Photo"
                print(f"  - {row[0]}: {row[1]} ({row[2]}) - {photo_status}")

        print("\n" + "=" * 50)
        print("✅ Database inspection complete!")


if __name__ == "__main__":
    try:
        inspect_database()
    except Exception as e:
        print(f"❌ Error connecting to database: {e}")
        print("Make sure your credentials.env file has the correct database settings.")
