"""AccountType Enum for Quotex API."""
from enum import IntEnum


class AccountType(IntEnum):
    """
    Enum for account types (REAL and DEMO).
    
    Inherits from IntEnum to ensure compatibility with integer-based
    API payloads (0 for REAL, 1 for DEMO).
    """
    REAL = 0
    DEMO = 1

    def __str__(self) -> str:
        return str(self.value)
