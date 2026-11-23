"""Trusted host-side TCP relay into a single isolated container."""

import socket
import socketserver
import subprocess
from threading import BoundedSemaphore, Lock, Thread


_CONTAINER_RELAY = """
import os
import socket
import sys
from threading import Thread

with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=5) as peer:
    peer.settimeout(None)
    def upload():
        try:
            while data := os.read(0, 65536):
                peer.sendall(data)
            peer.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    Thread(target=upload, daemon=True).start()
    while data := peer.recv(65536):
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
"""

class LoopbackRelay(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self, container: str, host: str, port: int):
        self.command = ["docker", "exec", "-i", container, "python", "-c", _CONTAINER_RELAY, host, str(port)]
        self.slots = BoundedSemaphore(64)
        self.connections: set[socket.socket] = set()
        self.guard = Lock()
        self.closed = False
        super().__init__(("127.0.0.1", 0), _Connection)
        Thread(target=self.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def close(self) -> None:
        with self.guard:
            if self.closed:
                return
            self.closed = True
            for connection in self.connections:
                _disconnect(connection)
        self.shutdown()
        self.server_close()


def _disconnect(connection: socket.socket) -> None:
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class _Connection(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        if not self.server.slots.acquire(blocking=False):
            return
        try:
            with self.server.guard:
                if self.server.closed:
                    return
                self.server.connections.add(self.request)
            try:
                self._relay()
            finally:
                with self.server.guard:
                    self.server.connections.discard(self.request)
        finally:
            self.server.slots.release()

    def _relay(self) -> None:
        with subprocess.Popen(
            self.server.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as docker:
            def upload() -> None:
                try:
                    with docker.stdin:
                        while data := self.request.recv(65536):
                            docker.stdin.write(data)
                            docker.stdin.flush()
                except OSError:
                    pass

            sender = Thread(target=upload, daemon=True)
            sender.start()
            try:
                while data := docker.stdout.read1(65536):
                    self.wfile.write(data)
            except OSError:
                pass
            finally:
                _disconnect(self.request)
                if docker.poll() is None:
                    docker.kill()
                sender.join()
