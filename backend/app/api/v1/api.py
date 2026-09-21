from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.campaigns import router as campaigns_router
from app.api.v1.endpoints.campaign_upload import router as campaign_upload_router
from app.api.v1.endpoints.employees import router as employees_router
from app.api.v1.endpoints.employee_interview import router as employee_interview_router
from app.api.v1.endpoints.phone_numbers import router as phone_numbers_router
from app.api.v1.endpoints.calls import router as calls_router
from app.api.v1.endpoints.settings import router as settings_router
from app.api.v1.endpoints.integrations import router as integrations_router
from app.webhooks.router import router as webhooks_router
from app.api.v1.endpoints.billing import router as billing_router
from app.api.v1.endpoints.instant_leads import router as instant_leads_router
from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.debug import router as debug_router
from app.api.v1.endpoints.dashboard import router as dashboard_router
from app.api.v1.endpoints.coupons import router as coupons_router
from app.api.v1.endpoints.coupon_offers import admin_router as coupon_share_router, router as coupon_offers_router


api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(employees_router)
api_router.include_router(employee_interview_router)
api_router.include_router(phone_numbers_router)
api_router.include_router(calls_router)
api_router.include_router(campaigns_router)
api_router.include_router(campaign_upload_router)
api_router.include_router(webhooks_router)
api_router.include_router(billing_router)
api_router.include_router(settings_router)
api_router.include_router(integrations_router)
api_router.include_router(instant_leads_router)
api_router.include_router(admin_router)
api_router.include_router(debug_router)
api_router.include_router(dashboard_router)
api_router.include_router(coupons_router)
api_router.include_router(coupon_share_router)
api_router.include_router(coupon_offers_router)
