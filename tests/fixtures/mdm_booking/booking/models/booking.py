"""A confirmed slot. Reaches into the intake code: the scheme keeps objects apart from functions."""

from booking.intake import accept


class Booking:
    def __init__(self, slot: str, client: str) -> None:
        self.slot = slot
        self.client = client

    def again(self) -> object:
        return accept("repeat", self.client)
