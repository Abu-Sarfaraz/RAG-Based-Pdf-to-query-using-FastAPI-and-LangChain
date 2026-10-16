"""Authenticate uploads and bound actual bytes before multipart spooling."""
import json

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool


def bearer_token(authorization):
    if not authorization or len(authorization) > 200:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


class _UploadTooLarge(HTTPException):
    def __init__(self, detail):
        super().__init__(413, detail)


async def send_error(send, status, detail):
    payload = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(payload)).encode())]})
    await send({"type": "http.response.body", "body": payload})


class UploadBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "").removeprefix(scope.get("root_path", "")).rstrip("/")
        if scope["type"] != "http" or scope.get("method") != "POST" or path not in ("/documents", "/ingest"):
            return await self.app(scope, receive, send)
        state = scope["app"].state
        headers = {key.lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        token = bearer_token(headers.get(b"authorization"))
        owner = await run_in_threadpool(state.catalog.authenticate, token) if token else None
        if owner is None:
            return await send_error(send, 401, "Restore or create a workspace to continue.")

        # Allow multipart headers as well as the file's separately enforced limit.
        limit = state.config.max_upload_mb * 1024 * 1024 + 1024 * 1024
        detail = f"Please upload a PDF smaller than {state.config.max_upload_mb} MB."
        declared = headers.get(b"content-length")
        if declared is not None:
            if not declared.isascii() or not declared.isdecimal():
                return await send_error(send, 400, "Invalid Content-Length header.")
            normalized = declared.lstrip("0") or "0"
            bound = str(limit)
            if len(normalized) > len(bound) or (len(normalized) == len(bound) and normalized > bound):
                return await send_error(send, 413, detail)

        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    # Starlette closes partially parsed multipart files on errors.
                    raise _UploadTooLarge(detail)
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _UploadTooLarge:
            # FastAPI normally handles this HTTPException; also support raw ASGI.
            await send_error(send, 413, detail)
