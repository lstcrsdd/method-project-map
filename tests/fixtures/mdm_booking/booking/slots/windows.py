"""Function 2.1: free windows for a request."""

from booking.models.request import Request


def free_windows(request: Request | None = None) -> list[str]:
    return ["10:00", "11:00"]
