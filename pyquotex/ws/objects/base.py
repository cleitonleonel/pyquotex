"""Module for Quotex websocket object."""


class Base(object):
    """Class for Quotex Base websocket object."""

    def __init__(self) -> None:
        self.__name: str | None = None

    @property
    def name(self) -> str | None:
        """Property to get websocket object name.
        :returns: The name of websocket object.
        """
        return self.__name
