"""User model for python_project fixture."""

from utils.helpers import format_name
from .profile import Profile
from ..services.api import ApiClient
import typing
import collections


class User:
    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email
        self.profile = Profile(name)

    def greet(self) -> str:
        return f"Hello, {format_name(self.name)}!"

    def to_dict(self) -> dict:
        return {"name": self.name, "email": self.email}
