from sqlalchemy import inspect, text, select

from app.db.base import Base
from app.db.session import engine
from app.db.session import SessionLocal

# Import models so SQLAlchemy registers all tables before create_all runs.
from app import models  # noqa: F401
from app.core.config import get_settings
from app.services.auth import hash_password
from app.models.tenant import Tenant
from app.models.user import User


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_users_for_local_auth()
    _ensure_auth_columns()
    _ensure_employee_provider_columns()
    _ensure_call_dispatch_columns()
    _ensure_billing_columns()
    _ensure_tenant_reseller_columns()
    _ensure_phone_number_lifecycle_columns()
    _ensure_employee_builder_columns()
    _ensure_tenant_feature_columns()
    _ensure_integration_tables()
    _ensure_instant_lead_columns()
    _ensure_campaign_execution_columns()
    _bootstrap_admin()


def _column_type(type_name: str) -> str:
    """Translate the small set of legacy SQLite type names for PostgreSQL."""
    if engine.dialect.name == "postgresql":
        return type_name.replace("DATETIME", "TIMESTAMP")
    return type_name

def _bootstrap_admin() -> None:
    settings = get_settings()
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        return
    with SessionLocal() as db:
        email = settings.bootstrap_admin_email.lower()
        if db.scalar(select(User.id).where(User.email == email)):
            return
        tenant = Tenant(name="Pontis Administration", slug="pontis-admin", status="active")
        db.add(tenant)
        db.flush()
        db.add(User(tenant_id=tenant.id, email=email, password_hash=hash_password(settings.bootstrap_admin_password), role="admin", status="active"))
        db.commit()


def _migrate_users_for_local_auth() -> None:
    """Add the local-auth column without replacing the users table.

    The legacy provider identity column is intentionally retained: it is
    harmless for the current model and retaining it avoids a destructive
    startup migration on either database.
    """
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    columns = {column["name"] for column in inspect(engine).get_columns("users")}
    if "auth_user_id" not in columns or "password_hash" in columns:
        return
    with engine.begin() as connection:
                connection.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)"))

def _ensure_auth_columns() -> None:
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    existing = {column["name"] for column in inspect(engine).get_columns("users")}
    with engine.begin() as connection:
        if "must_change_password" not in existing:
            connection.execute(text("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE"))

def _instant_lead_source_additions(dialect_name: str) -> dict[str, tuple[str, str]]:
    """Return additive column definitions with dialect-valid boolean defaults."""
    boolean_default = "TRUE" if dialect_name == "postgresql" else "1"
    return {
        "name": ("VARCHAR(255)", "'Instant Leads source'"),
        "source_type": ("VARCHAR(32)", "'google_sheet'"),
        "integration_key": ("VARCHAR(64)", "NULL"),
        "frequency_minutes": ("INTEGER", "5"),
        "spreadsheet_id": ("VARCHAR(255)", "NULL"),
        "sheet_name": ("VARCHAR(255)", "NULL"),
        "timezone": ("VARCHAR(64)", "NULL"),
        "auto_call": ("BOOLEAN NOT NULL", boolean_default),
        "working_hours": ("JSON", "NULL"),
        "daily_call_limit": ("INTEGER", "NULL"),
        "last_result": ("JSON", "NULL"),
    }


def _ensure_instant_lead_columns() -> None:
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    with engine.begin() as connection:
        source_columns = {c["name"] for c in inspect(engine).get_columns("instant_lead_sources")}
        additions = _instant_lead_source_additions(engine.dialect.name)
        for column, (kind, default) in additions.items():
            if column not in source_columns:
                connection.execute(text(f"ALTER TABLE instant_lead_sources ADD COLUMN {column} {kind} DEFAULT {default}"))
        row_columns = {c["name"] for c in inspect(engine).get_columns("instant_lead_rows")}
        for column, kind in {"external_record_id": "VARCHAR(255)", "status": "VARCHAR(32) NOT NULL DEFAULT 'new'"}.items():
            if column not in row_columns:
                connection.execute(text(f"ALTER TABLE instant_lead_rows ADD COLUMN {column} {kind}"))


def _ensure_employee_provider_columns() -> None:
    """Add nullable provider fields for existing SQLite installations.

    The project does not yet have a migration runner; these additive columns
    keep existing local databases compatible with the agent mapping model.
    """
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    existing = {column["name"] for column in inspect(engine).get_columns("ai_employee_versions")}
    additions = {
        "provider_name": "VARCHAR(100)",
        "provider_agent_id": "VARCHAR(255)",
        "provider_status": "VARCHAR(100)",
        "provider_metadata": "JSON",
    }
    with engine.begin() as connection:
        for column, column_type in additions.items():
            if column not in existing:
                connection.execute(text(f"ALTER TABLE ai_employee_versions ADD COLUMN {column} {_column_type(column_type)}"))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_employee_versions_provider_agent "
            "ON ai_employee_versions(provider_name, provider_agent_id) "
            "WHERE provider_agent_id IS NOT NULL"
        ))


def _ensure_call_dispatch_columns() -> None:
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    existing = {column["name"] for column in inspect(engine).get_columns("calls")}
    additions = {
        "employee_version_id": "CHAR(32)",
        "dispatch_metadata": "JSON",
    }
    with engine.begin() as connection:
        for column, column_type in additions.items():
            if column not in existing:
                connection.execute(text(f"ALTER TABLE calls ADD COLUMN {column} {column_type}"))


def _ensure_billing_columns() -> None:
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    with engine.begin() as connection:
        usage_columns = {column["name"] for column in inspect(engine).get_columns("usage_records")}
        for column, column_type in {
            "duration_seconds": "INTEGER",
            "unit_price": "NUMERIC(18, 4)",
            "currency": "VARCHAR(16) NOT NULL DEFAULT 'INR'",
        }.items():
            if column not in usage_columns:
                connection.execute(text(f"ALTER TABLE usage_records ADD COLUMN {column} {column_type}"))
        transaction_columns = {column["name"] for column in inspect(engine).get_columns("credit_transactions")}
        if "balance_before" not in transaction_columns:
            connection.execute(text("ALTER TABLE credit_transactions ADD COLUMN balance_before NUMERIC(18, 4)"))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_records_call_id "
            "ON usage_records(call_id) WHERE call_id IS NOT NULL"
        ))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_transactions_call_id "
            "ON credit_transactions(call_id) WHERE call_id IS NOT NULL"
        ))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_transactions_reference "
            "ON credit_transactions(reference_type, reference_id) "
            "WHERE reference_type IS NOT NULL AND reference_id IS NOT NULL"
        ))
        billing_columns = {column["name"] for column in inspect(engine).get_columns("billing_transactions")}
        if "provider_payment_id" not in billing_columns:
            connection.execute(text("ALTER TABLE billing_transactions ADD COLUMN provider_payment_id VARCHAR(255)"))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_transactions_provider_reference "
            "ON billing_transactions(provider_reference) WHERE provider_reference IS NOT NULL"
        ))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_transactions_provider_payment_id "
            "ON billing_transactions(provider_payment_id) WHERE provider_payment_id IS NOT NULL"
        ))


def _ensure_tenant_reseller_columns() -> None:
    """Additive reseller fields for existing SQLite development databases."""
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    existing = {column["name"] for column in inspect(engine).get_columns("tenants")}
    additions = {
        "omni_reseller_user_id": "VARCHAR(64)",
        "omni_reseller_status": "VARCHAR(64)",
        "omni_reseller_kyc_status": "VARCHAR(64)",
        "omni_reseller_region": "VARCHAR(8)",
        "omni_reseller_verified_at": "DATETIME",
        "omni_reseller_contact_phone": "VARCHAR(32)",
    }
    with engine.begin() as connection:
        for column, column_type in additions.items():
            if column not in existing:
                connection.execute(text(f"ALTER TABLE tenants ADD COLUMN {column} {_column_type(column_type)}"))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_tenants_omni_reseller_user_id "
            "ON tenants(omni_reseller_user_id) WHERE omni_reseller_user_id IS NOT NULL"
        ))


def _ensure_phone_number_lifecycle_columns() -> None:
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    existing = {column["name"] for column in inspect(engine).get_columns("phone_numbers")}
    with engine.begin() as connection:
        for column, kind in {"release_idempotency_key": "VARCHAR(64)", "release_failure_reason": "VARCHAR(64)", "released_at": "DATETIME"}.items():
            if column not in existing:
                connection.execute(text(f"ALTER TABLE phone_numbers ADD COLUMN {column} {_column_type(kind)}"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_phone_numbers_release_key ON phone_numbers(release_idempotency_key) WHERE release_idempotency_key IS NOT NULL"))


def _ensure_employee_builder_columns() -> None:
    if engine.dialect.name not in {"sqlite", "postgresql"}: return
    existing = {column["name"] for column in inspect(engine).get_columns("employee_interview_sessions")}
    with engine.begin() as connection:
        if "suggested_questions" not in existing:
            connection.execute(text("ALTER TABLE employee_interview_sessions ADD COLUMN suggested_questions JSON NOT NULL DEFAULT '[]'"))
        if "consumed_questions" not in existing:
            connection.execute(text("ALTER TABLE employee_interview_sessions ADD COLUMN consumed_questions JSON NOT NULL DEFAULT '[]'"))


def _ensure_tenant_feature_columns() -> None:
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    existing = {column["name"] for column in inspect(engine).get_columns("tenants")}
    with engine.begin() as connection:
        if "instant_leads_enabled" not in existing:
            connection.execute(text("ALTER TABLE tenants ADD COLUMN instant_leads_enabled BOOLEAN NOT NULL DEFAULT 1"))


def _ensure_campaign_execution_columns() -> None:
    """Add phone_number_id to campaigns for execution engine."""
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    existing = {column["name"] for column in inspect(engine).get_columns("campaigns")}
    with engine.begin() as connection:
        if "phone_number_id" not in existing:
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN phone_number_id CHAR(32)"))


def _ensure_integration_tables() -> None:
    """Additive migration for integration_connections and oauth_states tables."""
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        if "integration_connections" not in existing_tables:
            connection.execute(text(_column_type("""
                CREATE TABLE integration_connections (
                    id CHAR(32) NOT NULL PRIMARY KEY,
                    tenant_id CHAR(32) NOT NULL REFERENCES tenants(id),
                    integration_key VARCHAR(64) NOT NULL,
                    display_name VARCHAR(255) NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'not_connected',
                    external_account_reference VARCHAR(512),
                    provider_credentials TEXT,
                    configuration JSON,
                    connected_at DATETIME,
                    error_message VARCHAR(512),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_integration_connections_tenant_key UNIQUE (tenant_id, integration_key)
                )
            """)))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_integration_connections_tenant_id "
                "ON integration_connections(tenant_id)"
            ))
        if "oauth_states" not in existing_tables:
            connection.execute(text(_column_type("""
                CREATE TABLE oauth_states (
                    id CHAR(32) NOT NULL PRIMARY KEY,
                    tenant_id CHAR(32) NOT NULL REFERENCES tenants(id),
                    integration_key VARCHAR(64) NOT NULL,
                    state_token VARCHAR(128) NOT NULL UNIQUE,
                    expires_at DATETIME NOT NULL,
                    used BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            """)))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_oauth_states_state_token ON oauth_states(state_token)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_oauth_states_tenant_id ON oauth_states(tenant_id)"
            ))
