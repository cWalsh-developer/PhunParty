from dotenv import load_dotenv
import os
from pathlib import Path
import psycopg2
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

env_path = Path(__file__).resolve().parents[1] / "credentials.env"
load_dotenv(dotenv_path=env_path)

DatabaseURL = f"postgresql://{os.getenv('DB_User')}:{os.getenv('DB_Password')}@{os.getenv('DB_Host')}:{os.getenv('DB_Port')}/{os.getenv('DB_Name')}"

engine = create_engine(DatabaseURL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
