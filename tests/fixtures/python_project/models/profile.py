"""User profile submodule."""

from typing import Optional


class Profile:
    def __init__(self, username: str, bio: Optional[str] = None):
        self.username = username
        self.bio = bio or ""

    def set_bio(self, bio: str) -> None:
        self.bio = bio
