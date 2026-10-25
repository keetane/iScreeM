from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_runtime_schema() -> None:
    """Apply additive columns needed by existing local SQLite databases."""
    inspector = inspect(engine)
    if "pockets" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("pockets")}
    additions = {
        "volume": "FLOAT",
        "estimated_max_mw": "FLOAT",
        "volume_method": "VARCHAR(64)",
    }
    with engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE pockets ADD COLUMN {name} {sql_type}"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

