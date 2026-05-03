from datetime import datetime
from decimal import Decimal
from typing import Any, Optional


def format_datetime(dt: Optional[datetime]) -> Optional[str]:
    """Format datetime to ISO 8601 string."""
    if dt is None:
        return None
    return dt.isoformat()


def format_decimal(value: Optional[Decimal], places: int = 3) -> Optional[str]:
    """Format decimal to fixed decimal places string."""
    if value is None:
        return None
    return f"{value:.{places}f}"


def format_currency(amount: Decimal, currency: str = "NGN") -> str:
    """Format currency amount with symbol."""
    symbols = {"NGN": "₦", "USD": "$", "EUR": "€", "GBP": "£"}
    symbol = symbols.get(currency, currency)
    return f"{symbol}{amount:,.2f}"


def sanitize_string(value: Optional[str], max_length: int = 500) -> Optional[str]:
    """Strip and truncate a string value."""
    if value is None:
        return None
    return value.strip()[:max_length]


def operation_number_year(operation_number: str) -> Optional[int]:
    """Extract year from operation number like RA-2026-0001."""
    try:
        parts = operation_number.split("-")
        if len(parts) >= 2:
            return int(parts[1])
    except (ValueError, IndexError):
        pass
    return None
