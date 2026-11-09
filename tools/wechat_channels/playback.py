"""Serve only explicitly registered completed videos to the local HTML player."""
from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import socket
import threading


@dataclass
class _Video:
    path: Path
    cancelled: threading.Event = field(default_factory=threading.Event)
    clients: set = field(default_factory=set)


def _byte_range(value, size):
    """Parse the single byte range supported by an HTML video element."""
    if value is None:
        return 0, size - 1, False
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value.strip())
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError('Invalid range')
    first, last = match.groups()
    if not first:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError('Invalid suffix')
        return max(0, size - suffix), size - 1, True
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise ValueError('Unsatisfied range')
    return start, end, True


class PlaybackServer:
    """A random capability URL never contains a remote URL, key or file path."""
    def __init__(self):
        self._lock = threading.RLock()
        self._videos = {}
        self._closed = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def handle(self):
                try:
                    super().handle()
                except OSError:
                    # Revocation intentionally interrupts keep-alive reads too.
                    pass

            def log_message(self, _format, *_args):
                pass

            def do_GET(self):
                self._serve(False)

            def do_HEAD(self):
                self._serve(True)

            def _empty(self, status, size=None):
                self.send_response(status)
                self.send_header('Content-Length', '0')
                self.send_header('Cache-Control', 'no-store')
                if size is not None:
                    self.send_header('Content-Range', f'bytes */{size}')
                self.end_headers()

            def _serve(self, head):
                # Do not answer requests addressed through a different hostname
                # (including DNS rebinding); filenames never form part of URLs.
                if self.headers.get('Host') != owner._authority:
                    self._empty(403)
                    return
                with owner._lock:
                    entry = owner._videos.get(self.path)
                    if entry is not None and not entry.cancelled.is_set():
                        entry.clients.add(self.connection)
                    else:
                        entry = None
                if entry is None:
                    self._empty(404)
                    return
                try:
                    with entry.path.open('rb') as stream:
                        size = entry.path.stat().st_size
                        try:
                            start, end, partial = _byte_range(self.headers.get('Range'), size)
                        except (ValueError, OverflowError):
                            self._empty(416, size)
                            return
                        self.send_response(206 if partial else 200)
                        self.send_header('Content-Type', 'video/mp4')
                        self.send_header('Content-Length', str(max(0, end - start + 1)))
                        self.send_header('Accept-Ranges', 'bytes')
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        if partial:
                            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                        self.end_headers()
                        if head:
                            return
                        stream.seek(start)
                        remaining = end - start + 1
                        while remaining > 0 and not entry.cancelled.is_set():
                            data = stream.read(min(256 * 1024, remaining))
                            if not data:
                                break
                            self.wfile.write(data)
                            remaining -= len(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                except OSError:
                    # Never include a local path or operating-system exception.
                    self.close_connection = True
                finally:
                    with owner._lock:
                        entry.clients.discard(self.connection)

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = True
        self._authority = f'127.0.0.1:{self._server.server_address[1]}'
        self._thread = threading.Thread(target=self._server.serve_forever,
                                       kwargs={'poll_interval': .1}, daemon=True,
                                       name='ClipGo player')
        self._thread.start()

    def register(self, path):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError('Preview unavailable')
        with self._lock:
            if self._closed:
                raise RuntimeError('Player closed')
            self.revoke_all()
            route = '/video/' + secrets.token_urlsafe(32)
            self._videos[route] = _Video(path)
            return f'http://{self._authority}{route}'

    def revoke_all(self):
        with self._lock:
            for entry in self._videos.values():
                entry.cancelled.set()
                for connection in tuple(entry.clients):
                    try:
                        # A paused browser can stop consuming a large response.
                        # Wake a blocked write so its Windows file handle closes.
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            self._videos.clear()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.revoke_all()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)
