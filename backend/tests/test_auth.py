from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.core.config import get_settings
from app.db.base import Base
from app.main import app
from app.models import Lead, Tenant, User
from app.services.auth import JWT_ALGORITHM, create_access_token, hash_password
from app.services.tenant_scope import tenant_select


def test_authentication_lifecycle_and_tenant_isolation():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/auth/register", json={"email": "owner@example.com", "password": "safe-password"})
            assert response.status_code == 201
            payload = response.json()
            assert payload["access_token"]
            assert payload["user"]["email"] == "owner@example.com"
            user = db.get(User, UUID(payload["user"]["id"]))
            assert user.password_hash != "safe-password"

            assert client.post("/api/v1/auth/register", json={"email": "owner@example.com", "password": "safe-password"}).status_code == 409
            assert client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "wrong-password"}).status_code == 401
            login = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "safe-password"})
            assert login.status_code == 200
            token = login.json()["access_token"]
            assert client.get("/api/v1/auth/me").status_code == 401
            assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
            assert client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 204

            inactive = User(tenant_id=user.tenant_id, email="inactive@example.com", password_hash=hash_password("safe-password"), status="inactive")
            db.add(inactive)
            db.commit()
            assert client.post("/api/v1/auth/login", json={"email": "inactive@example.com", "password": "safe-password"}).status_code == 403

            tenant_b = Tenant(name="Tenant B", slug="tenant-b")
            user_b = User(tenant=tenant_b, email="other@example.com", password_hash=hash_password("safe-password"), status="active")
            db.add_all([tenant_b, user_b])
            db.flush()
            db.add_all([Lead(tenant_id=user.tenant_id, first_name="A", phone_number="+10000000001"), Lead(tenant_id=tenant_b.id, first_name="B", phone_number="+10000000002")])
            db.commit()
            assert [lead.first_name for lead in db.scalars(tenant_select(Lead, user.tenant_id))] == ["A"]
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_invalid_and_expired_tokens_are_rejected():
    with TestClient(app) as client:
        assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid"}).status_code == 401
        expired = jwt.encode(
            {"sub": "00000000-0000-0000-0000-000000000000", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
            get_settings().auth_secret_key,
            algorithm=JWT_ALGORITHM,
        )
        assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_health_endpoint_still_works():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
