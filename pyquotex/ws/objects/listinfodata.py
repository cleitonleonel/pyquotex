from typing import Any

from pyquotex.ws.objects.base import Base


class ListInfoData(Base):
    """Class for Quotex Candles websocket object."""

    def __init__(self) -> None:
        super(ListInfoData, self).__init__()
        self.__name = "listInfoData"
        self.listinfodata_dict: dict[str | int, dict[str, Any]] = {}

    def set(self, win: str, game_state: int, id_number: str | int, profit: float | int = 0) -> None:
        self.listinfodata_dict[id_number] = {
            "win": win,
            "game_state": game_state,
            "profit": profit
        }

    def delete(self, id_number: str | int) -> None:
        if id_number in self.listinfodata_dict:
            del self.listinfodata_dict[id_number]

    def get(self, id_number: str | int) -> dict[str, Any] | None:
        return self.listinfodata_dict.get(id_number)
