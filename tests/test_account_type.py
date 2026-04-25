import unittest

from pyquotex.utils.account_type import AccountType


class TestAccountType(unittest.TestCase):
    def test_enum_members(self):
        """Verify that AccountType has REAL and DEMO members."""
        self.assertEqual(AccountType.REAL, 0)
        self.assertEqual(AccountType.DEMO, 1)

    def test_int_compatibility(self):
        """Verify that AccountType works as an integer."""
        self.assertEqual(int(AccountType.REAL), 0)
        self.assertEqual(int(AccountType.DEMO), 1)
        self.assertTrue(AccountType.REAL == 0)
        self.assertTrue(AccountType.DEMO == 1)

    def test_string_representation(self):
        """Verify the string representation of Enum members returns the value."""
        self.assertEqual(str(AccountType.REAL), "0")
        self.assertEqual(str(AccountType.DEMO), "1")


if __name__ == "__main__":
    unittest.main()
