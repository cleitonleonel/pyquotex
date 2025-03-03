from quotexapi.ws.objects.base import Base


class Candle(object):
    """Class for Quotex candle."""

    def __init__(self, candle_data):
        """
        :param candle_data: The list of candles data.
        """
        self.__candle_data = candle_data

    @property
    def candle_time(self):
        """Property to get candle time.

        :returns: The candle time.
        """
        return self.__candle_data[0]

    @property
    def candle_open(self):
        """Property to get candle open value.

        :returns: The candle open value.
        """
        return self.__candle_data[1]

    @property
    def candle_close(self):
        """Property to get candle close value.

        :returns: The candle close value.
        """
        return self.__candle_data[2]

    @property
    def candle_high(self):
        """Property to get candle high value.

        :returns: The candle high value.
        """
        return self.__candle_data[3]

    @property
    def candle_low(self):
        """Property to get candle low value.

        :returns: The candle low value.
        """
        return self.__candle_data[4]

    @property
    def candle_type(self):
        """Property to get candle type value.

        :returns: The candle type value.
        """
        if self.candle_open < self.candle_close:
            return "green"
        elif self.candle_open > self.candle_close:
            return "red"


class Candles(Base):
    """Class for Quotex Candles websocket object."""

    def __init__(self):
        super(Candles, self).__init__()
        self.__name = "candles"
        self.__candles_data = None

    @property
    def candles_data(self):
        """Property to get candles data.

        :returns: The list of candles data.
        """
        return self.__candles_data

    @candles_data.setter
    def candles_data(self, candles_data):
        """Method to set candles data."""
        self.__candles_data = candles_data

    @property
    def first_candle(self):
        """Method to get first candle.

        :returns: The instance of :class:`Candle
            <quotexapi.ws.objects.candles.Candle>`.
        """
        return Candle(self.candles_data[0])

    @property
    def second_candle(self):
        """Method to get second candle.

        :returns: The instance of :class:`Candle
            <quotexapi.ws.objects.candles.Candle>`.
        """
        return Candle(self.candles_data[1])

    @property
    def current_candle(self):
        """Method to get current candle.

        :returns: The instance of :class:`Candle
            <quotexapi.ws.objects.candles.Candle>`.
        """
        return Candle(self.candles_data[-1])
