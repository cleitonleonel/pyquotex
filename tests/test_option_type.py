"""Tests for the shared OTC / option-type resolver."""
from pyquotex.utils.option_type import (
    OptionType,
    is_otc_asset,
    resolve_option_type,
)


def test_is_otc_asset_lowercase():
    assert is_otc_asset("EURUSD_otc") is True


def test_is_otc_asset_uppercase():
    assert is_otc_asset("EURUSD_OTC") is True


def test_is_otc_asset_regular():
    assert is_otc_asset("EURUSD") is False


def test_buy_otc_blitz_uses_blitz_type():
    """OTC + fast option still resolves to BLITZ — Quotex permits OTC blitz."""
    assert resolve_option_type(
        "EURUSD_otc", 30, is_fast_option=True, is_pending=False
    ) is OptionType.BLITZ


def test_buy_otc_non_fast_uses_digital():
    assert resolve_option_type(
        "EURUSD_otc", 60, is_fast_option=False, is_pending=False
    ) is OptionType.DIGITAL_OTC


def test_buy_regular_fast_uses_blitz():
    assert resolve_option_type(
        "EURUSD", 30, is_fast_option=True, is_pending=False
    ) is OptionType.BLITZ


def test_buy_regular_non_fast_uses_binary():
    assert resolve_option_type(
        "EURUSD", 60, is_fast_option=False, is_pending=False
    ) is OptionType.BINARY


def test_pending_otc_uses_digital_even_when_fast_flag_passed():
    """Pending orders on OTC always resolve to DIGITAL_OTC."""
    assert resolve_option_type(
        "EURUSD_otc", 60, is_fast_option=True, is_pending=True
    ) is OptionType.DIGITAL_OTC


def test_pending_regular_uses_binary():
    assert resolve_option_type(
        "EURUSD", 60, is_fast_option=False, is_pending=True
    ) is OptionType.BINARY


def test_option_type_int_values():
    """Wire values must match Quotex's expectations."""
    assert int(OptionType.BINARY) == 1
    assert int(OptionType.BLITZ) == 3
    assert int(OptionType.DIGITAL_OTC) == 100
