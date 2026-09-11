import httpx
import pytest
import logging
from types import SimpleNamespace
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db
from app.integrations.omnidimension import OmniDimensionClient, OmniDimensionPhoneNumberProvider
from app.integrations.omnidimension.reseller import OmniDimensionResellerProvider
from app.integrations.omnidimension.exceptions import OmniDimensionClientError
from app.integrations.omnidimension.exceptions import OmniDimensionServerError
from app.main import app
from app.models import PhoneNumber, Tenant, User
from app.services import auth as auth_service
from app.services.phone_numbers import (
    PhoneNumberAssignmentError,
    PhoneNumberMarketplaceService,
    PhoneNumberService,
    get_phone_number_marketplace_service,
)
from app.services.reseller_kyc import ResellerKycService, map_kyc_error
from app.services.auth import AuthenticatedUser
from app.schemas.phone_number import MarketplaceSearchRead


@pytest.fixture()
def phone_database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.db.base import Base

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    tenant_a = Tenant(name="Phone A", slug="phone-a")
    tenant_b = Tenant(name="Phone B", slug="phone-b")
    db.add_all([tenant_a, tenant_b])
    db.flush()
    db.add_all([
        User(tenant_id=tenant_a.id, password_hash=auth_service.hash_password("password123"), email="a@phone.test", status="active"),
        User(tenant_id=tenant_b.id, password_hash=auth_service.hash_password("password123"), email="b@phone.test", status="active"),
    ])
    db.commit()
    try:
        yield db, tenant_a, tenant_b
    finally:
        db.close()


@pytest.mark.parametrize("status_code", [401, 403, 404, 422, 429, 500, 503])
def test_live_kyc_requirements_route_preserves_provider_failure_category(phone_database, status_code):
    db, tenant, _ = phone_database
    secret = "route-test-secret"

    def handler(request):
        assert request.method == "GET"
        assert str(request.url) == "https://omnidim.io/api/v1/reseller/kyc/requirements?region=IN&carrier=carrier-1"
        return httpx.Response(status_code, json={"message": secret})

    client = OmniDimensionClient(
        settings=SimpleNamespace(omnidimension_api_key=secret, omnidimension_base_url="https://omnidim.io/api/v1", omnidimension_timeout_seconds=3, environment="test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=db.query(User).first(), tenant=tenant)
    from app.services.phone_numbers import get_reseller_kyc_service
    app.dependency_overrides[get_reseller_kyc_service] = lambda: ResellerKycService(OmniDimensionResellerProvider(client))
    try:
        response = TestClient(app).get("/api/v1/phone-numbers/kyc/requirements?region=IN&carrier=carrier-1")
        assert response.status_code == 502
        assert str(status_code) in response.json()["detail"]
        assert secret not in response.text
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_live_kyc_requirements_route_emits_diagnostic_and_parses_success(phone_database, caplog):
    db, tenant, _ = phone_database
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"region": "IN", "steps": [{"step": "verify-pan", "required": ["pan"]}]})

    client = OmniDimensionClient(
        settings=SimpleNamespace(omnidimension_api_key="diagnostic-secret", omnidimension_base_url="https://omnidim.io/api/v1", omnidimension_timeout_seconds=3, environment="test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=db.query(User).first(), tenant=tenant)
    from app.services.phone_numbers import get_reseller_kyc_service
    app.dependency_overrides[get_reseller_kyc_service] = lambda: ResellerKycService(OmniDimensionResellerProvider(client))
    try:
        with caplog.at_level(logging.WARNING, logger="app.integrations.omnidimension.client"):
            response = TestClient(app).get("/api/v1/phone-numbers/kyc/requirements?region=IN&carrier=carrier-1")
        assert response.status_code == 200
        assert response.json()["steps"][0]["step"] == "verify-pan"
        assert seen["url"] == "https://omnidim.io/api/v1/reseller/kyc/requirements?region=IN&carrier=carrier-1"
        diagnostic = " ".join(caplog.messages)
        assert "method=GET" in diagnostic and "response_status=200" in diagnostic
        assert "diagnostic-secret" not in diagnostic and "Authorization" not in diagnostic
    finally:
        client.close()
        app.dependency_overrides.clear()


class FakeProvider:
    def __init__(self, numbers):
        self.numbers = numbers

    def list_phone_numbers(self):
        return list(self.numbers)


def provider_number(provider_id="101", number="+15550000001", label="sales-line"):
    from app.integrations.omnidimension.phone_numbers import ProviderPhoneNumber

    return ProviderPhoneNumber(
        provider_id=provider_id,
        e164_number=number,
        status="active",
        label=label,
        metadata={"location": "US", "number_source": "imported"},
    )


def authenticated_client(db, user_id, monkeypatch):
    monkeypatch.setattr(auth_service, "decode_access_token", lambda token: db.query(User).all()[0 if user_id.endswith("-a") else 1].id)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_provider_adapter_uses_documented_endpoint_and_maps_response():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"success": True, "phone_numbers": [{
            "id": 213,
            "name": "sales-line",
            "phone_number": "+15551234567",
            "location": "US",
            "number_provider": "sip",
            "number_source": "imported",
            "can_message": False,
        }]})

    client = OmniDimensionClient(
        settings=type("Settings", (), {
            "omnidimension_api_key": "test-key",
            "omnidimension_base_url": "https://provider.test/api/v1",
            "omnidimension_timeout_seconds": 5,
        })(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        numbers = OmniDimensionPhoneNumberProvider(client).list_phone_numbers()
        assert seen["url"] == "https://provider.test/api/v1/phone_number/list?pageno=1&pagesize=150"
        assert numbers[0].provider_id == "213"
        assert numbers[0].e164_number == "+15551234567"
        assert numbers[0].metadata["number_source"] == "imported"
    finally:
        client.close()


def test_sync_is_idempotent_and_preserves_assignment(phone_database):
    db, tenant_a, _ = phone_database
    service = PhoneNumberService(FakeProvider([provider_number()]))
    first = service.sync_phone_numbers(db)
    assert first.created == 1
    local = db.scalar(select(PhoneNumber).where(PhoneNumber.provider_phone_number_id == "101"))
    assert local is not None
    service.assign_to_tenant(db, local.id, tenant_a.id)

    second = service.sync_phone_numbers(db)
    assert second.created == 0
    assert second.updated == 1
    assert db.scalar(select(PhoneNumber).where(PhoneNumber.provider_phone_number_id == "101")).tenant_id == tenant_a.id
    assert len(db.scalars(select(PhoneNumber)).all()) == 1


def test_sync_updates_records_and_does_not_delete_absent_numbers(phone_database):
    db, _, _ = phone_database
    service = PhoneNumberService(FakeProvider([provider_number(label="old")]))
    service.sync_phone_numbers(db)
    service.provider.numbers = [provider_number("102", label="new", number="+15550000002")]
    service.sync_phone_numbers(db)
    records = db.scalars(select(PhoneNumber)).all()
    assert len(records) == 2
    assert {record.e164_number for record in records} == {"+15550000001", "+15550000002"}
    assert db.scalar(select(PhoneNumber).where(PhoneNumber.provider_phone_number_id == "101")).label == "old"


def test_exclusive_assignment_cannot_be_duplicated(phone_database):
    db, tenant_a, tenant_b = phone_database
    service = PhoneNumberService(FakeProvider([provider_number()]))
    service.sync_phone_numbers(db)
    local = db.scalar(select(PhoneNumber).where(PhoneNumber.provider_phone_number_id == "101"))
    service.assign_to_tenant(db, local.id, tenant_a.id)
    with pytest.raises(PhoneNumberAssignmentError):
        service.assign_to_tenant(db, local.id, tenant_b.id)


def test_tenant_api_returns_only_assigned_numbers(phone_database, monkeypatch):
    db, tenant_a, tenant_b = phone_database
    service = PhoneNumberService(FakeProvider([provider_number("101", "+15550000001"), provider_number("102", "+15550000002")]))
    service.sync_phone_numbers(db)
    numbers = db.scalars(select(PhoneNumber).order_by(PhoneNumber.e164_number)).all()
    service.assign_to_tenant(db, numbers[0].id, tenant_a.id)
    service.assign_to_tenant(db, numbers[1].id, tenant_b.id)
    client_a = authenticated_client(db, "phone-user-a", monkeypatch)
    try:
        with client_a:
            response = client_a.get("/api/v1/phone-numbers", headers={"Authorization": "Bearer a"})
            assert response.status_code == 200
            assert [item["e164_number"] for item in response.json()] == ["+15550000001"]
            assert "tenant_id" not in response.json()[0]
            assert "test-key" not in response.text
    finally:
        app.dependency_overrides.clear()

    client_b = authenticated_client(db, "phone-user-b", monkeypatch)
    try:
        with client_b:
            response = client_b.get("/api/v1/phone-numbers", headers={"Authorization": "Bearer b"})
            assert [item["e164_number"] for item in response.json()] == ["+15550000002"]
    finally:
        app.dependency_overrides.clear()


def test_phone_api_requires_authentication():
    with TestClient(app) as client:
        assert client.get("/api/v1/phone-numbers").status_code == 401


def test_provider_failure_is_not_exposed_as_a_secret(phone_database):
    class FailedProvider:
        def list_phone_numbers(self):
            raise OmniDimensionServerError(503)

    with pytest.raises(OmniDimensionServerError) as error:
        PhoneNumberService(FailedProvider()).sync_phone_numbers(phone_database[0])
    assert "api" not in str(error.value).lower()


def test_marketplace_provider_uses_search_endpoint_and_normalizes_response():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"numbers": [{
            "phone_number": "+918000000001", "region": "IN", "monthly_rental_usd": 5.06,
            "validity_days": 30, "kyc_required": True,
        }], "total": 1, "page": 2, "limit": 10, "carrier_label": "Carrier 1"})

    client = OmniDimensionClient(settings=type("Settings", (), {
        "omnidimension_api_key": "test-key", "omnidimension_base_url": "https://provider.test/api/v1",
        "omnidimension_timeout_seconds": 5,
    })(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        response = PhoneNumberMarketplaceService(OmniDimensionPhoneNumberProvider(client), Decimal("650")).search(
            region="IN", carrier="carrier-1", pattern="800", page=2, limit=10
        )
        assert seen["url"] == "https://provider.test/api/v1/phone_number/search?region=IN&carrier=carrier-1&page=2&limit=10&pattern=800"
        assert response.numbers[0].phone_number == "+918000000001"
        assert response.numbers[0].customer_monthly_price_inr == Decimal("650")
        assert response.numbers[0].kyc_required is True
        assert "provider" not in response.numbers[0].model_dump_json()
    finally:
        client.close()


def test_marketplace_preserves_provider_returned_carrier():
    class MarketplaceProvider:
        def search_available_numbers(self, **_):
            from app.integrations.omnidimension.phone_numbers import ProviderAvailablePhoneNumber

            return ([ProviderAvailablePhoneNumber(
                e164_number="+918000000001", region="IN", carrier="carrier-2-new",
                monthly_rental_usd=None, validity_days=30, kyc_required=True,
            )], 1, 1, 20, "Carrier 2")

    response = PhoneNumberMarketplaceService(MarketplaceProvider(), Decimal("650")).search(
        region="IN", carrier="carrier-1", pattern=None, page=1, limit=20
    )
    assert response.numbers[0].carrier == "carrier-2-new"


def test_marketplace_validation_requires_exact_number_region_and_carrier():
    class MarketplaceProvider:
        def search_available_numbers(self, **_):
            from app.integrations.omnidimension.phone_numbers import ProviderAvailablePhoneNumber

            return ([ProviderAvailablePhoneNumber(
                e164_number="+918000000001", region="IN", carrier="carrier-2-new",
                monthly_rental_usd=None, validity_days=None, kyc_required=True,
            )], 1, 1, 20, None)

    service = PhoneNumberMarketplaceService(MarketplaceProvider(), Decimal("650"))
    assert service.validate_selected_number(
        phone_number="+918000000001", region="IN", carrier="carrier-2-new"
    ) is True
    assert service.validate_selected_number(
        phone_number="+918000000001", region="IN", carrier="carrier-1"
    ) is False


def test_reseller_status_uses_child_user_id_only_and_selects_carrier(phone_database):
    db, tenant, _ = phone_database
    tenant.omni_reseller_user_id = "child-a"
    seen = {}

    class Provider:
        def get_kyc_status(self, *, user_id):
            seen["user_id"] = user_id
            return {"regions": [
                {"region": "IN", "carrier": "carrier-1", "status": "completed", "next_step": None, "can_purchase": True, "kyc_required": True},
                {"region": "IN", "carrier": "carrier-2-new", "status": "in_progress", "next_step": "register", "can_purchase": False, "kyc_required": True},
            ]}

    from app.services.reseller_kyc import ResellerKycService
    result = ResellerKycService(Provider()).status(db, tenant=tenant, region="IN", carrier="carrier-2-new")
    assert seen == {"user_id": "child-a"}
    assert result["carrier"] == "carrier-2-new"
    assert result["next_step"] == "register"
    assert result["can_purchase"] is False


def test_reseller_adapter_status_does_not_send_phone_number():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"regions": []})

    client = OmniDimensionClient(settings=type("Settings", (), {
        "omnidimension_api_key": "test-key", "omnidimension_base_url": "https://provider.test/api/v1",
        "omnidimension_timeout_seconds": 5,
    })(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        OmniDimensionResellerProvider(client).get_kyc_status(user_id="child-a")
        assert seen["url"] == "https://provider.test/api/v1/reseller/kyc/status?user_id=child-a"
        assert "phone_number" not in seen["url"]
    finally:
        client.close()


def test_kyc_step_conflict_returns_refreshed_next_step(phone_database):
    db, tenant, _ = phone_database
    user = db.query(User).filter(User.tenant_id == tenant.id).first()
    tenant.omni_reseller_user_id = "child-a"

    class Provider:
        def submit_kyc_step(self, **_):
            raise OmniDimensionClientError(409)

        def get_kyc_status(self, *, user_id):
            assert user_id == "child-a"
            return {"regions": [{
                "region": "IN", "carrier": "carrier-1", "status": "in_progress",
                "next_step": "verify-pan", "can_purchase": False, "kyc_required": True,
            }]}

    from app.api.v1.endpoints.phone_numbers import get_reseller_kyc_service
    from app.services.auth import AuthenticatedUser
    from app.services.phone_numbers import get_phone_number_marketplace_service
    from app.services.reseller_kyc import ResellerKycService

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=user, tenant=tenant)
    app.dependency_overrides[get_phone_number_marketplace_service] = lambda: type(
        "Marketplace", (), {"validate_selected_number": lambda self, **kwargs: True}
    )()
    app.dependency_overrides[get_reseller_kyc_service] = lambda: ResellerKycService(Provider())
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/phone-numbers/kyc/step", json={
                "step": "aadhaar-verify", "region": "IN", "carrier": "carrier-1",
                "phone_number": "+918045440456", "values": {},
            })
        assert response.status_code == 409
        assert response.json()["detail"]["next_step"] == "verify-pan"
        assert "phone_number" not in response.request.url.query.decode()
    finally:
        app.dependency_overrides.clear()


def test_marketplace_api_requires_authentication():
    with TestClient(app) as client:
        assert client.get("/api/v1/phone-numbers/marketplace?region=IN&carrier=carrier-1").status_code == 401


def test_marketplace_api_is_read_only_and_secret_free(phone_database, monkeypatch):
    db, tenant, _ = phone_database

    class Marketplace:
        def search(self, **_):
            return MarketplaceSearchRead(numbers=[], total=0, page=1, limit=20)

    from app.services.auth import AuthenticatedUser

    client = TestClient(app)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=db.query(User).first(), tenant=tenant)
    app.dependency_overrides[get_phone_number_marketplace_service] = lambda: Marketplace()
    try:
        with client:
            response = client.get("/api/v1/phone-numbers/marketplace?region=IN&carrier=carrier-1")
            assert response.status_code == 200
            assert response.json() == {"numbers": [], "total": 0, "page": 1, "limit": 20}
            assert "test-key" not in response.text
            assert db.scalars(select(PhoneNumber)).all() == []
    finally:
        app.dependency_overrides.clear()


def test_reseller_kyc_is_tenant_scoped_and_never_persists_step_values(phone_database):
    db, tenant_a, tenant_b = phone_database

    class Reseller:
        def initialize(self, db, *, tenant, user, phone):
            assert tenant.id == tenant_a.id
            tenant.omni_reseller_user_id = "child-a"
            tenant.omni_reseller_contact_phone = phone
            db.commit()
            return {"region": "IN", "status": "new", "next_step": "verify-pan", "can_purchase": False}

        def status(self, db, *, tenant, carrier=None):
            if tenant.omni_reseller_user_id:
                return {"region": "IN", "status": "new", "next_step": "verify-pan", "can_purchase": False}
            return {"region": "IN", "status": "not_started", "next_step": None, "can_purchase": False, "needs_contact_phone": True}

        def requirements(self, *, region, carrier):
            return {"region": region, "carrier": carrier, "steps": [{"step": "verify-pan", "required": ["pan"], "cooldown": False}]}

        def submit(self, db, *, tenant, step, region, carrier, values):
            assert tenant.id == tenant_a.id and step == "verify-pan" and values["pan"] == "ABCDE1234F"
            tenant.omni_reseller_kyc_status = "pan_verified"
            db.commit()
            return {"region": region, "status": "pan_verified", "next_step": "aadhaar-otp", "can_purchase": False}

    from app.services.auth import AuthenticatedUser
    from app.services.phone_numbers import get_reseller_kyc_service
    app.dependency_overrides[get_phone_number_marketplace_service] = lambda: type("Marketplace", (), {
        "validate_selected_number": lambda self, **kwargs: True,
    })()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=db.query(User).first(), tenant=tenant_a)
    app.dependency_overrides[get_reseller_kyc_service] = lambda: Reseller()
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/phone-numbers/kyc/status").json()["needs_contact_phone"] is True
            assert client.post("/api/v1/phone-numbers/kyc/initialize", json={"phone": "+919876543210"}).status_code == 200
            assert client.get("/api/v1/phone-numbers/kyc/requirements?region=IN&carrier=carrier-1&phone_number=%2B919876543210").json()["steps"][0]["step"] == "verify-pan"
            response = client.post("/api/v1/phone-numbers/kyc/step", json={"step": "verify-pan", "region": "IN", "carrier": "carrier-1", "phone_number": "+919876543210", "values": {"pan": "ABCDE1234F"}})
            assert response.status_code == 200 and response.json()["next_step"] == "aadhaar-otp"
            db.refresh(tenant_a)
            assert tenant_a.omni_reseller_kyc_status == "pan_verified"
            assert "ABCDE1234F" not in str(tenant_a.__dict__)
            assert tenant_b.omni_reseller_user_id is None
    finally:
        app.dependency_overrides.clear()
