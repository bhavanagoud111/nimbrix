"""botocore Stubber helpers: deterministic AWS responses with no network or credentials."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.stub import Stubber


class StubbedSession:
    """Quacks like boto3.Session but hands out pre-stubbed clients per service name."""

    def __init__(self, *services: str, region: str = "us-east-1") -> None:
        fake = boto3.Session(
            aws_access_key_id="testing", aws_secret_access_key="testing", region_name=region
        )
        self.clients = {
            name: fake.client(name, region_name=region)  # type: ignore[call-overload]
            for name in services
        }
        self.stubbers = {name: Stubber(client) for name, client in self.clients.items()}
        for stubber in self.stubbers.values():
            stubber.activate()

    def client(self, name: str, **_: Any) -> Any:
        return self.clients[name]

    def stub(self, service: str) -> Stubber:
        return self.stubbers[service]

    def assert_no_pending(self) -> None:
        for stubber in self.stubbers.values():
            stubber.assert_no_pending_responses()
