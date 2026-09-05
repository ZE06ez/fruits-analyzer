from __future__ import annotations

import http.client
import io


class _HandlerSocket:
    def __init__(self, request: bytes) -> None:
        self._request = io.BytesIO(request)
        self._response = io.BytesIO()

    def makefile(self, mode: str, buffering: int | None = None):
        if "r" in mode:
            return self._request
        if "w" in mode:
            return self._response
        raise ValueError(f"unsupported socket mode: {mode}")

    def sendall(self, data: bytes) -> None:
        self._response.write(data)

    def response_bytes(self) -> bytes:
        return self._response.getvalue()


class _ResponseSocket:
    def __init__(self, response: bytes) -> None:
        self._response = io.BytesIO(response)

    def makefile(self, mode: str, buffering: int | None = None):
        if "r" not in mode:
            raise ValueError(f"unsupported response mode: {mode}")
        return self._response


class _DummyServer:
    server_name = "127.0.0.1"
    server_port = 0

    def shutdown(self) -> None:
        return None


class InProcessHttpClient:
    def __init__(self, handler_class) -> None:
        self.handler_class = handler_class
        self.server = _DummyServer()

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        payload = body or b""
        request_headers = {
            "Host": "127.0.0.1",
            "Connection": "close",
            **(headers or {}),
        }
        if payload and "Content-Length" not in request_headers:
            request_headers["Content-Length"] = str(len(payload))

        head = [f"{method} {path} HTTP/1.1"]
        head.extend(f"{key}: {value}" for key, value in request_headers.items())
        raw_request = ("\r\n".join(head) + "\r\n\r\n").encode("iso-8859-1") + payload

        socket = _HandlerSocket(raw_request)
        self.handler_class(socket, ("127.0.0.1", 0), self.server)

        response = http.client.HTTPResponse(_ResponseSocket(socket.response_bytes()))
        response.begin()
        return response
