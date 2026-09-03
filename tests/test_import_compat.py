"""Verify legacy import paths continue to resolve."""


def test_stable_api_quotex_importable():
    from pyquotex.stable_api import Quotex

    assert Quotex is not None
    assert hasattr(Quotex, "buy")
    assert hasattr(Quotex, "get_balance")
    assert hasattr(Quotex, "connect")


def test_quotex_api_importable():
    from pyquotex.api import QuotexAPI

    assert QuotexAPI is not None


def test_account_type_importable():
    from pyquotex.utils.account_type import AccountType

    assert AccountType.DEMO is not None
    assert AccountType.REAL is not None


def test_indicators_importable():
    from pyquotex.utils.indicators import TechnicalIndicators

    assert TechnicalIndicators is not None


def test_exceptions_importable():
    from pyquotex.exceptions import QuotexTimeoutError

    assert issubclass(QuotexTimeoutError, Exception)
