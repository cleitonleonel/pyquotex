from pyquotex.ws.objects.base import Base


class Profile(Base):
    """Class for Quotex Profile websocket object."""

    def __init__(self) -> None:
        super(Profile, self).__init__()
        self.__name = "profile"
        self.__nick_name: str | None = None
        self.__profile_id: int | str | None = None
        self.__avatar: str | None = None
        self.__country: str | None = None
        self.__country_name: str | None = None
        self.__live_balance: float | None = None
        self.__demo_balance: float | None = None
        self.__msg: str | None = None
        self.__currency_code: str | None = None
        self.__currency_symbol: str | None = None
        self.__profile_level: str | int | None = None
        self.__minimum_amount: float | int | None = None
        self.__offset: int | None = None

    @property
    def nick_name(self) -> str | None:
        """Property to get nick_name value.

        :returns: The nick_name value.
        """
        return self.__nick_name

    @nick_name.setter
    def nick_name(self, nick_name: str) -> None:
        """Method to set nick_name value."""
        self.__nick_name = nick_name

    @property
    def live_balance(self) -> float | None:
        """Property to get live_balance value.

        :returns: The live_balance value.
        """
        return self.__live_balance

    @live_balance.setter
    def live_balance(self, live_balance: float) -> None:
        """Method to set live_balance value."""
        self.__live_balance = live_balance

    @property
    def profile_id(self) -> int | str | None:
        """Property to get profile value.

        :returns: The profile value.
        """
        return self.__profile_id

    @profile_id.setter
    def profile_id(self, profile_id: int | str) -> None:
        """Method to set profile value."""
        self.__profile_id = profile_id

    @property
    def demo_balance(self) -> float | None:
        """Property to get demo_balance value.

        :returns: The demo_balance value.
        """
        return self.__demo_balance

    @demo_balance.setter
    def demo_balance(self, demo_balance: float) -> None:
        """Method to set demo_balance value."""
        self.__demo_balance = demo_balance

    @property
    def avatar(self) -> str | None:
        """Property to get avatar value.

        :returns: The avatar value.
        """
        return self.__avatar

    @avatar.setter
    def avatar(self, avatar: str) -> None:
        """Method to set avatar value."""
        self.__avatar = avatar

    @property
    def msg(self) -> str | None:
        return self.__msg

    @msg.setter
    def msg(self, msg: str) -> None:
        self.__msg = msg

    @property
    def currency_symbol(self) -> str | None:
        return self.__currency_symbol

    @currency_symbol.setter
    def currency_symbol(self, currency_symbol: str) -> None:
        self.__currency_symbol = currency_symbol

    @property
    def country(self) -> str | None:
        return self.__country

    @country.setter
    def country(self, country: str) -> None:
        self.__country = country

    @property
    def offset(self) -> int | None:
        return self.__offset

    @offset.setter
    def offset(self, offset: int) -> None:
        self.__offset = offset

    @property
    def country_name(self) -> str | None:
        return self.__country_name

    @country_name.setter
    def country_name(self, country_name: str) -> None:
        self.__country_name = country_name

    @property
    def minimum_amount(self) -> float | int | None:
        return self.__minimum_amount

    @property
    def currency_code(self) -> str | None:
        return self.__currency_code

    @currency_code.setter
    def currency_code(self, currency_code: str) -> None:
        self.__currency_code = currency_code
        if self.__currency_code and self.__currency_code.upper() == "BRL":
            self.__minimum_amount = 5

    @property
    def profile_level(self) -> str | int | None:
        return self.__profile_level

    @profile_level.setter
    def profile_level(self, profile_level: str | int) -> None:
        self.__profile_level = profile_level
