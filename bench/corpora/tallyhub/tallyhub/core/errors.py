"""Domain errors shared across tallyhub."""


class TallyError(Exception):
    """Base class for all domain errors."""


class CurrencyMismatch(TallyError):
    def __init__(self, left: str, right: str) -> None:
        super().__init__(f"currency mismatch: {left} vs {right}")
        self.left = left
        self.right = right


class NotFound(TallyError):
    def __init__(self, kind: str, key: str) -> None:
        super().__init__(f"{kind} {key!r} not found")
        self.kind = kind
        self.key = key


class ValidationError(TallyError):
    def __init__(self, problems: list) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


class InvalidTransition(TallyError):
    pass


class OutOfStock(TallyError):
    def __init__(self, sku: str, requested: int, available: int) -> None:
        super().__init__(f"{sku}: requested {requested}, available {available}")
        self.sku = sku
        self.requested = requested
        self.available = available


class PaymentDeclined(TallyError):
    pass


class RefundError(TallyError):
    pass


class Unauthorized(TallyError):
    pass
