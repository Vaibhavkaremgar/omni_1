from sqlalchemy import text
from sqlalchemy.dialects import postgresql, sqlite

from app.db.init_db import _instant_lead_source_additions


def test_instant_lead_postgresql_boolean_default_is_native_boolean():
    additions = _instant_lead_source_additions("postgresql")
    kind, default = additions["auto_call"]
    statement = text(f"ALTER TABLE instant_lead_sources ADD COLUMN auto_call {kind} DEFAULT {default}")
    compiled = statement.compile(dialect=postgresql.dialect()).string
    assert "BOOLEAN NOT NULL DEFAULT TRUE" in compiled
    assert "DEFAULT 1" not in compiled


def test_instant_lead_sqlite_boolean_default_remains_compatible():
    additions = _instant_lead_source_additions("sqlite")
    kind, default = additions["auto_call"]
    statement = text(f"ALTER TABLE instant_lead_sources ADD COLUMN auto_call {kind} DEFAULT {default}")
    compiled = statement.compile(dialect=sqlite.dialect()).string
    assert "BOOLEAN NOT NULL DEFAULT 1" in compiled


def test_instant_lead_additions_have_no_postgresql_integer_boolean_defaults():
    additions = _instant_lead_source_additions("postgresql")
    for column, (kind, default) in additions.items():
        if "BOOLEAN" in kind:
            assert default in {"TRUE", "FALSE"}, column
