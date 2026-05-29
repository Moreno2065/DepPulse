"""User profile submodule."""



class Profile:
    def __init__(self, username: str, bio: str | None = None):
        self.username = username
        self.bio = bio or ""

    def set_bio(self, bio: str) -> None:
        self.bio = bio
