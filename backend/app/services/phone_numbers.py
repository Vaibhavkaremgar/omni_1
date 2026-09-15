from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.omnidimension import OmniDimensionClient, OmniDimensionPhoneNumberProvider, OmniDimensionResellerProvider
from app.core.config import get_settings
from app.schemas.phone_number import MarketplaceNumberRead, MarketplaceSearchRead
from app.models.phone_number import PhoneNumber
from app.models.platform_demo_phone_access import PlatformDemoPhoneAccess
from app.models.enums import PhoneOwnership
from app.services.reseller_kyc import ResellerKycService
from app.services.phone_lifecycle import PhoneLifecycleService


class PhoneNumberAssignmentError(Exception):
    """Raised when an internal assignment cannot be completed safely."""


@dataclass(frozen=True)
class PhoneNumberSyncResult:
    created: int
    updated: int
    total: int


class PhoneNumberService:
    def __init__(self, provider: OmniDimensionPhoneNumberProvider):
        self.provider = provider

    def sync_phone_numbers(self, db: Session) -> PhoneNumberSyncResult:
        provider_numbers = self.provider.list_phone_numbers()
        existing = db.scalars(
            select(PhoneNumber).where(PhoneNumber.provider_name == "omnidimension")
        ).all()
        by_provider_id = {
            number.provider_phone_number_id: number
            for number in existing
            if number.provider_phone_number_id
        }
        by_e164 = {number.e164_number: number for number in existing}
        created = 0
        updated = 0
        seen_provider_ids: set[str] = set()

        for item in provider_numbers:
            if item.provider_id in seen_provider_ids:
                continue
            seen_provider_ids.add(item.provider_id)
            number = by_provider_id.get(item.provider_id) or by_e164.get(item.e164_number)
            if number is None:
                number = PhoneNumber(
                    tenant_id=None,
                    e164_number=item.e164_number,
                    provider_name="omnidimension",
                    provider_phone_number_id=item.provider_id,
                    status=item.status, ownership=PhoneOwnership.platform_demo.value,
                    label=item.label,
                    capabilities=item.metadata,
                )
                db.add(number)
                created += 1
            else:
                number.e164_number = item.e164_number
                number.provider_name = "omnidimension"
                number.provider_phone_number_id = item.provider_id
                number.status = item.status
                number.label = item.label
                number.capabilities = item.metadata
                updated += 1
            by_provider_id[item.provider_id] = number
            by_e164[item.e164_number] = number

        db.commit()
        return PhoneNumberSyncResult(created=created, updated=updated, total=len(provider_numbers))

    def assign_to_tenant(self, db: Session, phone_number_id: UUID, tenant_id: UUID) -> PhoneNumber:
        number = db.scalar(select(PhoneNumber).where(PhoneNumber.id == phone_number_id))
        if number is None:
            raise PhoneNumberAssignmentError("Phone number not found.")
        if number.tenant_id is not None and number.tenant_id != tenant_id:
            raise PhoneNumberAssignmentError("Phone number is already assigned.")
        number.tenant_id = tenant_id
        db.commit()
        db.refresh(number)
        return number

    @staticmethod
    def list_for_tenant(db: Session, tenant_id: UUID) -> list[PhoneNumber]:
        return list(
            db.scalars(
                select(PhoneNumber)
                .outerjoin(PlatformDemoPhoneAccess, PlatformDemoPhoneAccess.phone_number_id == PhoneNumber.id)
                .where((PhoneNumber.tenant_id == tenant_id) | (PlatformDemoPhoneAccess.tenant_id == tenant_id))
                .order_by(PhoneNumber.created_at.desc())
            ).all()
        )

    @staticmethod
    def can_use(db: Session, phone: PhoneNumber, tenant_id: UUID) -> bool:
        if phone.ownership == PhoneOwnership.tenant.value:
            return phone.tenant_id == tenant_id
        return db.scalar(select(PlatformDemoPhoneAccess.id).where(
            PlatformDemoPhoneAccess.phone_number_id == phone.id,
            PlatformDemoPhoneAccess.tenant_id == tenant_id,
        )) is not None


class PhoneNumberMarketplaceService:
    """Read-only marketplace facade. Search results are never database records."""

    def __init__(self, provider: OmniDimensionPhoneNumberProvider, monthly_price_inr: Decimal):
        self.provider = provider
        self.monthly_price_inr = monthly_price_inr

    def search(self, *, region: str, carrier: str, pattern: str | None, page: int, limit: int) -> MarketplaceSearchRead:
        available, total, result_page, result_limit, carrier_label = self.provider.search_available_numbers(
            region=region, carrier=carrier, pattern=pattern, page=page, limit=limit
        )
        numbers = []
        for item in available:
            # Provider price is deliberately not included in the customer response.
            # It remains available on the provider result for future internal accounting.
            numbers.append(MarketplaceNumberRead(
                phone_number=item.e164_number, region=item.region, carrier=item.carrier, carrier_label=carrier_label,
                customer_monthly_price_inr=self.monthly_price_inr,
                validity_days=item.validity_days, kyc_required=item.kyc_required,
            ))
        return MarketplaceSearchRead(numbers=numbers, total=total, page=result_page, limit=result_limit)

    def validate_selected_number(self, *, phone_number: str, region: str, carrier: str) -> bool:
        available, _, _, _, _ = self.provider.search_available_numbers(
            region=region, carrier=carrier, page=1, limit=150
        )
        return any(
            item.e164_number == phone_number
            and item.region == region
            and item.carrier == carrier
            for item in available
        )


def get_phone_number_marketplace_service() -> PhoneNumberMarketplaceService:
    settings = get_settings()
    return PhoneNumberMarketplaceService(
        OmniDimensionPhoneNumberProvider(OmniDimensionClient(settings)), settings.phone_number_monthly_price_inr
    )


def get_reseller_kyc_service() -> ResellerKycService:
    return ResellerKycService(OmniDimensionResellerProvider(OmniDimensionClient(get_settings())))


def get_phone_lifecycle_service() -> PhoneLifecycleService:
    return PhoneLifecycleService(OmniDimensionPhoneNumberProvider(OmniDimensionClient(get_settings())))
