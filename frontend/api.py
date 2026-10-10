"""Small HTTP client for workspace-scoped document chat."""
import json

import requests


class APIError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class API:
    def __init__(self, base_url, token=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.session = requests.Session()
        if token:
            self.session.headers["Authorization"] = "Bearer " + token

    def request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, self.base_url + path, timeout=(5, 100), **kwargs)
            if not response.ok:
                try:
                    detail = response.json().get("detail", "Please retry.")
                    if isinstance(detail, list):
                        detail = "; ".join(item.get("msg", "Invalid request") for item in detail)
                except ValueError:
                    detail = "The document service couldn't complete this request. Please retry."
                response.close()
                raise APIError(str(detail), response.status_code)
            return response
        except requests.Timeout as error:
            raise APIError("This request took too long. Please retry; your saved documents are still available.") from error
        except requests.RequestException as error:
            raise APIError("Can't reach the document service. Please retry in a moment.") from error

    def json(self, method, path, **kwargs):
        with self.request(method, path, **kwargs) as response:
            try:
                return response.json()
            except ValueError as error:
                raise APIError("The document service returned an unreadable response. Please retry.") from error

    def upload(self, filename, data):
        return self.json("POST", "/documents", files={"file": (filename, data, "application/pdf")})

    def stream(self, conversation_id, payload):
        try:
            with self.request("POST", f"/conversations/{conversation_id}/messages", json=payload, stream=True) as response:
                response.encoding = "utf-8"
                for line in response.iter_lines(decode_unicode=True, chunk_size=1):
                    if line and line.startswith("data: "):
                        yield json.loads(line[6:])
        except (requests.RequestException, ValueError) as error:
            raise APIError("The connection was interrupted. Your question is saved; please retry.") from error
