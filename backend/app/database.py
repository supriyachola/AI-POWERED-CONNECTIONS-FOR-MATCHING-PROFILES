import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./connectai.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def migrate_schema():
    """Small MVP-safe migration layer so existing Neon/SQLite databases gain new profile fields."""
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    dialect = engine.dialect.name
    profile_columns = {c["name"] for c in inspector.get_columns("profiles")}
    connection_columns = {c["name"] for c in inspector.get_columns("connections")}
    additions = {
        "age": "INTEGER",
        "gender": "VARCHAR(32) DEFAULT 'prefer_not_to_say'",
        "country": "VARCHAR(80) DEFAULT ''",
        "state": "VARCHAR(100) DEFAULT ''",
        "city": "VARCHAR(100) DEFAULT ''",
        "vibe": "VARCHAR(80) DEFAULT ''",
        "preferred_gender": "VARCHAR(32) DEFAULT 'any'",
        "preferred_country": "VARCHAR(80) DEFAULT 'any'",
        "preferred_state": "VARCHAR(100) DEFAULT 'any'",
        "min_age": "INTEGER DEFAULT 18",
        "max_age": "INTEGER DEFAULT 100",
    }
    with engine.begin() as conn:
        for name, definition in additions.items():
            if name not in profile_columns:
                conn.execute(text(f'ALTER TABLE profiles ADD COLUMN {name} {definition}'))
        if "updated_at" not in connection_columns:
            conn.execute(text("ALTER TABLE connections ADD COLUMN updated_at TIMESTAMP"))
            conn.execute(text("UPDATE connections SET updated_at = created_at WHERE updated_at IS NULL"))
        # Normalize old rows without overwriting user-entered values.
        if dialect == "postgresql":
            conn.execute(text("UPDATE profiles SET interests='[]' WHERE interests IS NULL OR interests=''"))
            conn.execute(text("UPDATE profiles SET gender='prefer_not_to_say' WHERE gender IS NULL OR gender=''"))
            conn.execute(text("UPDATE profiles SET preferred_gender='any' WHERE preferred_gender IS NULL OR preferred_gender=''"))
            conn.execute(text("UPDATE profiles SET preferred_country='any' WHERE preferred_country IS NULL OR preferred_country=''"))
            conn.execute(text("UPDATE profiles SET preferred_state='any' WHERE preferred_state IS NULL OR preferred_state=''"))
        else:
            conn.execute(text("UPDATE profiles SET interests='[]' WHERE interests IS NULL OR interests=''"))
