from enum import Enum


class TenantStatus(str, Enum):
    active = "active"
    suspended = "suspended"
    archived = "archived"


class UserRole(str, Enum):
    owner = "owner"
    admin = "admin"
    member = "member"


class UserStatus(str, Enum):
    invited = "invited"
    active = "active"
    disabled = "disabled"


class EmployeeStatus(str, Enum):
    draft = "draft"
    review = "review"
    published = "published"
    paused = "paused"
    archived = "archived"


class EmployeeCallType(str, Enum):
    inbound = "inbound"
    outbound = "outbound"
    both = "both"


class EmployeeCreationMode(str, Enum):
    chat = "chat"
    talk = "talk"


class VersionStatus(str, Enum):
    draft = "draft"
    review = "review"
    published = "published"
    archived = "archived"


class NumberStatus(str, Enum):
    provisioning = "provisioning"
    active = "active"
    inactive = "inactive"
    released = "released"


class CampaignStatus(str, Enum):
    draft = "draft"
    scheduled = "scheduled"
    running = "running"
    paused = "paused"
    completed = "completed"
    stopped = "stopped"
    failed = "failed"
    archived = "archived"


class ContactStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    called = "called"
    failed = "failed"
    completed = "completed"
    do_not_call = "do_not_call"
    skipped = "skipped"


class LeadStatus(str, Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    disqualified = "disqualified"
    converted = "converted"
    archived = "archived"


class CallDirection(str, Enum):
    inbound = "inbound"
    outbound = "outbound"


class CallStatus(str, Enum):
    queued = "queued"
    ringing = "ringing"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    no_answer = "no_answer"
    busy = "busy"
    voicemail = "voicemail"
    canceled = "canceled"


class WalletStatus(str, Enum):
    active = "active"
    frozen = "frozen"
    closed = "closed"


class CreditTransactionType(str, Enum):
    top_up = "top_up"
    call_usage = "call_usage"
    purchase = "purchase"
    consumption = "consumption"
    refund = "refund"
    adjustment = "adjustment"


class CreditTransactionStatus(str, Enum):
    pending = "pending"
    posted = "posted"
    reversed = "reversed"
    failed = "failed"


class UsageType(str, Enum):
    call_minutes = "call_minutes"
    recording_storage = "recording_storage"
    transcription = "transcription"
    model_tokens = "model_tokens"
    provider_cost = "provider_cost"
    campaign_action = "campaign_action"


class UsageUnit(str, Enum):
    minutes = "minutes"
    credits = "credits"
    tokens = "tokens"
    calls = "calls"
    events = "events"


class BillingTransactionType(str, Enum):
    charge = "charge"
    payment = "payment"
    refund = "refund"
    adjustment = "adjustment"
    invoice = "invoice"


class BillingTransactionStatus(str, Enum):
    pending = "pending"
    posted = "posted"
    paid = "paid"
    failed = "failed"
    void = "void"
