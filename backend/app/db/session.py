from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings


settings = get_settings()

# Railway may provide either postgres:// or postgresql:// URLs depending on the
# service/template. Explicitly select psycopg (v3); a bare PostgreSQL URL makes
# SQLAlchemy fall back to the legacy psycopg2 dialect.
database_url = settings.database_url
if database_url.startswith("postgres://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgres://"):]
elif database_url.startswith("postgresql://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgresql://"):]

engine_kwargs: dict[str, object] = {
    "future": True,
    "echo": settings.database_echo,
    "pool_pre_ping": True,
}

if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

else:
    # Keep a modest pool for the single Railway instance and recycle stale
    # connections that can be closed by the managed database.
    engine_kwargs.update(pool_size=5, max_overflow=10, pool_recycle=1800)

engine = create_engine(database_url, **engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
