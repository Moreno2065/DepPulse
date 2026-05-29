"""Main entry point for the python_project fixture."""

import json

from utils import compute_hash
from utils.helpers import format_name


def main():
    from models.user import User
    user = User("Alice", "alice@example.com")
    print(f"Hello, {format_name(user.name)}")
    print(f"Hash: {compute_hash(user.name)}")


class AppConfig:
    def __init__(self):
        self.settings = json.loads('{"debug": true}')

    def get(self, key, default=None):
        return self.settings.get(key, default)


if __name__ == "__main__":
    main()
