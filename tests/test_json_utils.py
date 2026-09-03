"""Tests for ``pyquotex.utils.json_utils``."""

import pytest

from pyquotex.utils import json_utils as j


@pytest.mark.unit
def test_dumps_returns_bytes() -> None:
    assert isinstance(j.dumps({"a": 1}), bytes)


@pytest.mark.unit
def test_dumps_bytes_is_alias() -> None:
    assert j.dumps_bytes({"a": 1}) == j.dumps({"a": 1})


@pytest.mark.unit
def test_dumps_str_returns_str() -> None:
    s = j.dumps_str({"a": 1})
    assert isinstance(s, str)
    assert '"a"' in s and "1" in s


@pytest.mark.unit
def test_roundtrip() -> None:
    payload = {"k": [1, 2, 3], "s": "x"}
    assert j.loads(j.dumps(payload)) == payload
    assert j.loads(j.dumps_str(payload)) == payload


@pytest.mark.unit
def test_has_orjson_flag_is_bool() -> None:
    assert isinstance(j.HAS_ORJSON, bool)
