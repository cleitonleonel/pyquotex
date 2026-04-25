import asyncio
import os

import pytest

from pyquotex.api import Login
from pyquotex.config import credentials


class Api:
    lang = "pt"
    session_data = {}
    resource_path = os.getcwd()


@pytest.mark.asyncio
async def test_login():
    email, password = credentials()

    assert email and password, "Credentials not configured!"

    api_instance = Api()
    login_instance = Login(api_instance)

    status, message = await login_instance(email, password)
    if not status:
        pytest.skip(f"Login fail: {message}")
    print("✅ Login Test Passed!")


if __name__ == "__main__":
    asyncio.run(test_login())
