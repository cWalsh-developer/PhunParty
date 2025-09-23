import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Load environment variables from credentials.env file (for local development)
env_path = Path(__file__).resolve().parents[1] / "credentials.env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

# Use DATABASE_URL if available (Render provides this), otherwise build from individual vars
DatabaseURL = os.getenv("DATABASE_URL")
if not DatabaseURL:
    # Fallback to individual environment variables
    db_user = os.getenv("DB_User")
    db_password = os.getenv("DB_Password")
    db_host = os.getenv("DB_Host")
    db_port = os.getenv("DB_Port")
    db_name = os.getenv("DB_Name")
    DatabaseURL = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

engine = create_engine(
    DatabaseURL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
