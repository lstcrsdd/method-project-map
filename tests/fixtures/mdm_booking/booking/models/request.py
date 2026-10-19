"""A client's request for a service."""


class Request:
    def __init__(self, service: str, phone: str) -> None:
        self.service = service
        self.phone = phone
