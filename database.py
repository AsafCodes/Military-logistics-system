import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# כרגע משתמשים ב-SQLite (קובץ מקומי) כדי שיהיה לך קל לרוץ
# Get DB URL from Env (Docker) or fallback to SQLite (Local)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./sql_app.db")

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

# יצירת המנוע
engine = create_engine(DATABASE_URL, connect_args=connect_args)

# יצירת ה-Session (דרכו אנחנו שומרים ושולפים נתונים)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# המחלקה הבסיסית שכל הטבלאות יירשו ממנה
Base = declarative_base()