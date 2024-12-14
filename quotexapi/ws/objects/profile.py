from quotexapi.ws.objects.base import Base


class Profile(Base):
    """Class for Quotex Profile websocket object."""

    def __init__(self):
        super(Profile, self).__init__()
        self.__name = "profile"
        self.__nick_name = None
        self.__profile_id = None
        self.__avatar = None
        self.__country = None
        self.__country_name = None
        self.__live_balance = None
        self.__demo_balance = None
        self.__msg = None
        self.__currency_code = None
        self.__currency_symbol = None
        self.__profile_level = None
        self.__minimum_amount = None
        self.__offset = None

    @property
    def nick_name(self):
        """Property to get nick_name value.

        :returns: The nick_name value.
        """
        return self.__nick_name

    @nick_name.setter
    def nick_name(self, nick_name):
        """Method to set nick_name value."""
        self.__nick_name = nick_name

    @property
    def live_balance(self):
        """Property to get live_balance value.

        :returns: The live_balance value.
        """
        return self.__live_balance

    @live_balance.setter
    def live_balance(self, live_balance):
        """Method to set live_balance value."""
        self.__live_balance = live_balance

    @property
    def profile_id(self):
        """Property to get profile value.

        :returns: The profile value.
        """
        return self.__profile_id

    @profile_id.setter
    def profile_id(self, profile_id):
        """Method to set profile value."""
        self.__profile_id = profile_id

    @property
    def demo_balance(self):
        """Property to get demo_balance value.

        :returns: The demo_balance value.
        """
        return self.__demo_balance

    @demo_balance.setter
    def demo_balance(self, demo_balance):
        """Method to set demo_balance value."""
        self.__demo_balance = demo_balance

    @property
    def avatar(self):
        """Property to get avatar value.

        :returns: The avatar value.
        """
        return self.__avatar

    @avatar.setter
    def avatar(self, avatar):
        """Method to set avatar value."""
        self.__avatar = avatar

    @property
    def msg(self):
        return self.__msg

    @msg.setter
    def msg(self, msg):
        self.__msg = msg

    @property
    def currency_symbol(self):
        return self.__currency_symbol

    @currency_symbol.setter
    def currency_symbol(self, currency_symbol):
        self.__currency_symbol = currency_symbol

    @property
    def country(self):
        return self.__country

    @country.setter
    def country(self, country):
        self.__country = country

    @property
    def offset(self):
        return self.__offset

    @offset.setter
    def offset(self, offset):
        self.__offset = offset

    @property
    def country_name(self):
        return self.__country_name

    @country_name.setter
    def country_name(self, country_name):
        self.__country_name = country_name

    @property
    def minimum_amount(self):
        return self.__minimum_amount

    @property
    def currency_code(self):
        return self.__currency_code

    @currency_code.setter
    def currency_code(self, currency_code):
        self.__currency_code = currency_code
        if self.__currency_code.upper() == "BRL":
            self.__minimum_amount = 5

    @property
    def profile_level(self):
        return self.__profile_level

    @profile_level.setter
    def profile_level(self, profile_level):
        self.__profile_level = profile_level
