"""Application-specific errors with actionable CLI messages."""


class MarketRiskLabError(Exception):
    """Base class for expected user-facing errors."""


class DataValidationError(MarketRiskLabError):
    """Raised when a specification or fixture violates its public schema."""


class RepositoryError(MarketRiskLabError):
    """Raised when persistence operations cannot be completed safely."""
