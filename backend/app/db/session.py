from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings


settings = get_settings()

# Railway may provide either postgres:// or postgresql:// URLs depending on the
# service/template. SQLAlchemy's PostgreSQL dialect expects the latter form.
database_url = settings.database_url
if database_url.startswith("postgres://"):
    database_url = "postgresql://" + database_url[len("postgres://"):]

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
