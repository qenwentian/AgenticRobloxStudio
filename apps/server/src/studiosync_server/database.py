import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def database(url: str | None = None):
    url = url or os.getenv("STUDIOSYNC_DATABASE_URL", "sqlite:///./studiosync.db")
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 30}
        if url.startswith("sqlite")
        else {"connect_timeout": 5, "options": "-c statement_timeout=15000 -c lock_timeout=10000"},
        pool_pre_ping=True,
    )
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def sqlite_settings(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")

    return engine, sessionmaker(engine, expire_on_commit=False)
