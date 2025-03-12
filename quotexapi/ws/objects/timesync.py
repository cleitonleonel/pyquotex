import time
import datetime
from quotexapi.ws.objects.base import Base

class TimeSync(Base):
    """Class to manage time synchronization for Quotex WebSocket."""

    def __init__(self):
        super().__init__()
        self.__name = "timeSync"
        self.__server_timestamp = time.time()
        self.__expiration_time_minutes = 1

    @property
    def server_timestamp(self):
        """Get the server timestamp.

        :returns: The server timestamp.
        """
        return self.__server_timestamp

    @server_timestamp.setter
    def server_timestamp(self, timestamp):
        """Set the server timestamp.

        :param timestamp: New timestamp to set.
        """
        if not isinstance(timestamp, (int, float)):
            raise ValueError("The timestamp must be a number.")
        self.__server_timestamp = timestamp

    @property
    def server_datetime(self):
        """Get the server date and time based on the timestamp.

        :returns: The server date and time.
        """
        return datetime.datetime.fromtimestamp(self.server_timestamp)

    @property
    def expiration_time(self):
        """Get the expiration time in minutes.

        :returns: The expiration time in minutes.
        """
        return self.__expiration_time_minutes

    @expiration_time.setter
    def expiration_time(self, minutes):
        """Set the expiration time in minutes.

        :param minutes: Expiration time in minutes.
        """
        if not isinstance(minutes, (int, float)) or minutes <= 0:
            raise ValueError("The expiration time must be a positive number.")
        self.__expiration_time_minutes = minutes

    @property
    def expiration_datetime(self):
        """Get the expiration date and time based on the expiration time and server timestamp.

        :returns: The expiration date and time.
        """
        return self.server_datetime + datetime.timedelta(minutes=self.expiration_time)

    @property
    def expiration_timestamp(self):
        """Get the expiration timestamp.

        :returns: The expiration timestamp.
        """
        return time.mktime(self.expiration_datetime.timetuple())

