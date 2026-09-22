class OmniDimensionError(Exception):
    """Base exception for controlled OmniDimension integration failures."""


class OmniDimensionConfigurationError(OmniDimensionError):
    """The backend is missing valid provider configuration."""


class OmniDimensionAuthenticationError(OmniDimensionError):
    """The provider rejected the configured credential."""

    def __init__(self, status_code: int = 401, provider_message: str | None = None):
        self.status_code = status_code
        self.provider_message = provider_message
        super().__init__(f"OmniDimension rejected the configured credentials (HTTP {status_code}).")


class OmniDimensionClientError(OmniDimensionError):
    """The provider rejected a request with a 4xx response."""

    def __init__(self, status_code: int, provider_code: str | None = None, provider_message: str | None = None):
        self.status_code = status_code
        self.provider_code = provider_code
        self.provider_message = provider_message
        super().__init__(f"OmniDimension rejected the request (HTTP {status_code}).")


class OmniDimensionServerError(OmniDimensionError):
    """The provider failed while handling a request."""

    def __init__(self, status_code: int, provider_code: str | None = None, provider_message: str | None = None):
        self.status_code = status_code
        self.provider_code = provider_code
        self.provider_message = provider_message
        super().__init__(f"OmniDimension returned a provider error (HTTP {status_code}).")


class OmniDimensionNetworkError(OmniDimensionError):
    """The request could not reach the provider or timed out."""

    def __init__(self, message: str, *, exception_class: str | None = None, exception_message: str | None = None):
        self.exception_class = exception_class
        self.exception_message = exception_message
        super().__init__(message)


class OmniDimensionResponseError(OmniDimensionError):
    """The provider returned a response that could not be consumed safely."""


class OmniDimensionPostCallConfigurationNotPersistedError(OmniDimensionResponseError):
    """The provider accepted an agent write but did not retain its webhook."""

    code = "provider_post_call_configuration_not_persisted"

    def __init__(self, *, agent_id: str, webhook_url: str, post_call_config_ids: object):
        self.agent_id = agent_id
        self.webhook_url = webhook_url
        self.post_call_config_ids = post_call_config_ids
        super().__init__("OmniDimension did not persist the post-call webhook configuration.")
