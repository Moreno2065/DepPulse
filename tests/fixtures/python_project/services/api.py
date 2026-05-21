"""API client for python_project fixture."""

from typing import Any
import json as _json


class ApiClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url

    def get(self, path: str) -> dict[str, Any]:
        return {"status": "ok", "path": path}

    def post(self, path: str, data: Any) -> dict[str, Any]:
        return {"status": "created", "data": data}
