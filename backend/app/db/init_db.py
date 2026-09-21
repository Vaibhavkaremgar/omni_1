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
    _ensure_employee_core_columns()
    _ensure_call_dispatch_columns()
    _ensure_call_post_call_columns()
    _ensure_billing_columns()
    _ensure_tenant_reseller_columns()
    _ensure_phone_number_lifecycle_columns()
    _ensure_phone_number_demo_columns()
    _ensure_employee_builder_columns()
    _ensure_tenant_feature_columns()
    _ensure_integration_tables()
    _ensure_instant_lead_columns()
    _ensure_campaign_execution_columns()
    _ensure_campaign_contact_columns()
    _ensure_employee_knowledge_file_columns()
    _bootstrap_admin()


def _column_type(type_name: str) -> str:
    """Translate the small set of legacy SQLite type names for PostgreSQL."""
    if engine.dialect.name == "postgresql":
        return type_name.replace("DATETIME", "TIMESTAMP")
    return type_name


def _boolean_default(value: bool) -> str:
    if engine.dialect.name == "postgresql":
        return "TRUE" if value else "FALSE"
    return "1" if value else "0"


def _ensure_employee_knowledge_file_columns() -> None:
    """Create the additive KB table for databases initialized before this feature."""
    if "employee_knowledge_files" not in inspect(engine).get_table_names():
        from app.models.employee_knowledge_file import EmployeeKnowledgeFile
        EmployeeKnowledgeFile.__table__.create(bind=engine, checkfirst=True)
    elif engine.dialect.name in {"sqlite", "postgresql"}:
        existing = {column["name"] for column in inspect(engine).get_columns("employee_knowledge_files")}
        if "knowledge_text" not in existing:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE employee_knowledge_files ADD COLUMN knowledge_text TEXT"))

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
    if "password_hash" in columns:
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


def _ensure_employee_core_columns() -> None:
    """Upgrade pre-builder employee tables without replacing existing data."""
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    existing = {column["name"] for column in inspect(engine).get_columns("ai_employees")}
    # These defaults mirror the current create schema and make historical rows
    # queryable without fabricating customer-specific business information.
    additions = {
        "purpose": "TEXT NOT NULL DEFAULT 'To be defined through the builder'",
        "call_type": "VARCHAR(32) NOT NULL DEFAULT 'inbound'",
        "llm_provider": "VARCHAR(100) NOT NULL DEFAULT 'legacy'",
        "llm_model": "VARCHAR(150) NOT NULL DEFAULT 'legacy'",
        "language": "VARCHAR(100) NOT NULL DEFAULT 'English'",
        "creation_mode": "VARCHAR(32) NOT NULL DEFAULT 'chat'",
    }
    with engine.begin() as connection:
        for column, column_type in additions.items():
            if column not in existing:
                connection.execute(text(f"ALTER TABLE ai_employees ADD COLUMN {column} {_column_type(column_type)}"))


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

def _ensure_call_post_call_columns() -> None:
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    existing = {column["name"] for column in inspect(engine).get_columns("calls")}
    additions = {
        "transcript_data": "JSON", "analysis_status": "VARCHAR(32)",
        "analysis_json": "JSON", "customer_intent": "VARCHAR(255)",
        "key_points": "JSON", "action_items": "JSON",
        "follow_up_required": "BOOLEAN", "follow_up_notes": "TEXT",
        "completed_at": "DATETIME",
    }
    with engine.begin() as connection:
        for column, kind in additions.items():
            if column not in existing:
                connection.execute(text(f"ALTER TABLE calls ADD COLUMN {column} {_column_type(kind)}"))


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
        wallet_columns = {column["name"] for column in inspect(engine).get_columns("credit_wallets")}
        if "promotional_minutes" not in wallet_columns:
            connection.execute(text("ALTER TABLE credit_wallets ADD COLUMN promotional_minutes NUMERIC(18, 4) NOT NULL DEFAULT 0"))
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


def _ensure_phone_number_demo_columns() -> None:
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    phone_inspector = inspect(engine)
    phone_columns = phone_inspector.get_columns("phone_numbers")
    existing = {c["name"] for c in phone_columns}
    with engine.begin() as connection:
        # Older local SQLite databases were created with tenant_id NOT NULL.
        # Platform/demo phones intentionally have no tenant owner, so rebuild
        # this one table additively while preserving every existing row.
        tenant_column = next((c for c in phone_columns if c["name"] == "tenant_id"), None)
        if engine.dialect.name == "sqlite" and tenant_column and not tenant_column.get("nullable", True):
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.exec_driver_sql("""
                CREATE TABLE phone_numbers_demo_migration (
                    tenant_id CHAR(32), label VARCHAR(255), e164_number VARCHAR(32) NOT NULL,
                    provider_name VARCHAR(100), provider_phone_number_id VARCHAR(255),
                    ownership VARCHAR(32) NOT NULL DEFAULT 'tenant', status VARCHAR(32) NOT NULL,
                    employee_id CHAR(32), campaign_id CHAR(32), capabilities JSON,
                    release_idempotency_key VARCHAR(64), release_failure_reason VARCHAR(64),
                    released_at DATETIME, id CHAR(32) NOT NULL PRIMARY KEY,
                    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_phone_numbers_tenant_e164_demo UNIQUE (tenant_id, e164_number),
                    FOREIGN KEY(tenant_id) REFERENCES tenants(id),
                    FOREIGN KEY(employee_id) REFERENCES ai_employees(id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO phone_numbers_demo_migration
                (tenant_id,label,e164_number,provider_name,provider_phone_number_id,ownership,status,
                 employee_id,campaign_id,capabilities,release_idempotency_key,release_failure_reason,
                 released_at,id,created_at,updated_at)
                SELECT tenant_id,label,e164_number,provider_name,provider_phone_number_id,
                       COALESCE(ownership,'tenant'),status,employee_id,campaign_id,capabilities,
                       release_idempotency_key,release_failure_reason,released_at,id,created_at,updated_at
                FROM phone_numbers
            """)
            connection.exec_driver_sql("DROP TABLE phone_numbers")
            connection.exec_driver_sql("ALTER TABLE phone_numbers_demo_migration RENAME TO phone_numbers")
            connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_phone_numbers_provider_id ON phone_numbers(provider_name, provider_phone_number_id) WHERE provider_phone_number_id IS NOT NULL")
            connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_phone_numbers_release_key ON phone_numbers(release_idempotency_key) WHERE release_idempotency_key IS NOT NULL")
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            existing = {c["name"] for c in inspect(engine).get_columns("phone_numbers")}
        if "ownership" not in existing:
            connection.execute(text("ALTER TABLE phone_numbers ADD COLUMN ownership VARCHAR(32) NOT NULL DEFAULT 'tenant'"))
        tables = set(inspect(engine).get_table_names())
        if "platform_demo_phone_access" not in tables:
            connection.execute(text(_column_type(f"""
                CREATE TABLE platform_demo_phone_access (
                    id CHAR(32) NOT NULL PRIMARY KEY,
                    phone_number_id CHAR(32) NOT NULL REFERENCES phone_numbers(id),
                    tenant_id CHAR(32) NOT NULL REFERENCES tenants(id),
                    CONSTRAINT uq_demo_phone_tenant UNIQUE (phone_number_id, tenant_id)
                )
            """)))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_demo_phone_access_phone ON platform_demo_phone_access(phone_number_id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_demo_phone_access_tenant ON platform_demo_phone_access(tenant_id)"))


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
            connection.execute(text(f"ALTER TABLE tenants ADD COLUMN instant_leads_enabled BOOLEAN NOT NULL DEFAULT {_boolean_default(True)}"))
        for column in ("notify_campaign_completed", "notify_low_balance"):
            if column not in existing:
                connection.execute(text(f"ALTER TABLE tenants ADD COLUMN {column} BOOLEAN NOT NULL DEFAULT {_boolean_default(True)}"))


def _ensure_campaign_execution_columns() -> None:
    """Add phone_number_id to campaigns for execution engine."""
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    existing = {column["name"] for column in inspect(engine).get_columns("campaigns")}
    with engine.begin() as connection:
        if "phone_number_id" not in existing:
            connection.execute(text("ALTER TABLE campaigns ADD COLUMN phone_number_id CHAR(32)"))
        additions = {"timezone": "VARCHAR(64)", "scheduled_at": "TIMESTAMP", "calling_window_start": "VARCHAR(8)", "calling_window_end": "VARCHAR(8)", "max_attempts": "INTEGER NOT NULL DEFAULT 3", "concurrency": "INTEGER NOT NULL DEFAULT 1", "retry_enabled": f"BOOLEAN NOT NULL DEFAULT {_boolean_default(True)}", "retry_intervals": "JSON"}
        for column, kind in additions.items():
            if column not in existing:
                connection.execute(text(f"ALTER TABLE campaigns ADD COLUMN {column} {kind}"))

def _ensure_campaign_contact_columns() -> None:
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    existing = {c["name"] for c in inspect(engine).get_columns("campaign_contacts")}
    additions = {
        "normalized_phone": "VARCHAR(32)",
        "customer_data": "JSON",
        "provider_request_id": "VARCHAR(255)",
        "provider_call_id": "VARCHAR(255)",
        "error_message": "VARCHAR(1024)",
        "claimed_at": "TIMESTAMP",
        "attempt_started_at": "TIMESTAMP",
        "completed_at": "TIMESTAMP",
        "retry_at": "TIMESTAMP",
        "callback_at": "TIMESTAMP",
        "lease_token": "VARCHAR(64)",
    }
    with engine.begin() as connection:
        for column, kind in additions.items():
            if column not in existing:
                connection.execute(text(f"ALTER TABLE campaign_contacts ADD COLUMN {column} {_column_type(kind)}"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_campaign_contacts_status ON campaign_contacts(status)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_campaign_contacts_normalized_phone ON campaign_contacts(normalized_phone)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_campaign_contacts_provider_request_id ON campaign_contacts(provider_request_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_campaign_contacts_provider_call_id ON campaign_contacts(provider_call_id)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_contact_phone ON campaign_contacts(campaign_id, normalized_phone)"))


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
                    used BOOLEAN NOT NULL DEFAULT {_boolean_default(False)},
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
