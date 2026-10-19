"""Delivery channels for customer messages (in-memory outboxes)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from tallyhub.core.registry import NOTIFICATION_CHANNELS
from tallyhub.customers.models import Customer


@dataclass(frozen=True)
class Message:
    channel: str
    to: str
    subject: str
    body: str


class Channel:
    name = "abstract"

    def __init__(self, outbox: List[Message]) -> None:
        self.outbox = outbox

    def address(self, customer: Customer) -> str:
        raise NotImplementedError

    def send(self, customer: Customer, content: Dict[str, str]) -> Message:
        message = Message(self.name, self.address(customer), content["subject"], content["body"])
        self.outbox.append(message)
        return message


@NOTIFICATION_CHANNELS.register("email")
class EmailChannel(Channel):
    name = "email"

    def address(self, customer: Customer) -> str:
        return customer.email


@NOTIFICATION_CHANNELS.register("sms")
class SmsChannel(Channel):
    name = "sms"

    def address(self, customer: Customer) -> str:
        return f"sms:{customer.id}"

    def send(self, customer: Customer, content: Dict[str, str]) -> Message:
        return super().send(customer, {"subject": "", "body": content["subject"]})
