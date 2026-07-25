import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

log = logging.getLogger("tangent.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema() -> None:
    """Add columns that exist on the models but not yet in the database.

    `create_all()` creates missing *tables* and silently ignores tables that
    already exist — so a new column on an existing model means every query
    selecting it fails against a live database. This closes that gap for the
    only case that matters here: additive columns.

    It deliberately never drops, renames, or retypes anything. Once the data is
    worth protecting properly, replace this with Alembic.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all handles brand-new tables
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                if not (column.nullable or column.server_default is not None):
                    log.warning(
                        "Skipping %s.%s: NOT NULL with no server default can't be "
                        "added to a populated table.", table.name, column.name,
                    )
                    continue
                ddl_type = column.type.compile(engine.dialect)
                conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}')
                )
                log.info("Added column %s.%s (%s)", table.name, column.name, ddl_type)
