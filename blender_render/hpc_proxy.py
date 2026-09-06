"""Multiplex TCP over one SSH/SLURM stdio channel; only forwards GPU loopback."""
from __future__ import annotations
import argparse
import base64
import json
import socket
import struct
import subprocess
import sys
import threading
from pathlib import Path

HEADER = struct.Struct("!BII")
OPEN, DATA, CLOSE = 1, 2, 3


class Multiplexer:
    def __init__(self, reader, writer, remote_port=None):
        self.reader, self.writer, self.remote_port = reader, writer, remote_port
        self.lock = threading.Lock()
        self.sockets = {}

    def send(self, kind, identifier, data=b""):
        with self.lock:
            # Newline framing survives SLURM's line-buffered task stdout.
            frame = json.dumps([kind, identifier, base64.b64encode(data).decode()], separators=(",", ":")).encode() + b"\n"
            self.writer.write(frame)
            self.writer.flush()

    def attach(self, identifier, connection):
        self.sockets[identifier] = connection
        def forward():
            try:
                while data := connection.recv(65536):
                    self.send(DATA, identifier, data)
            except (OSError, BrokenPipeError):
                pass
            finally:
                try: self.send(CLOSE, identifier)
                except (OSError, BrokenPipeError): pass
                self.close(identifier)
        threading.Thread(target=forward, daemon=True).start()

    def close(self, identifier):
        connection = self.sockets.pop(identifier, None)
        if connection:
            try: connection.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            connection.close()

    def read_exact(self, length):
        chunks = bytearray()
        while len(chunks) < length:
            data = self.reader.read(length - len(chunks))
            if not data:
                raise EOFError()
            chunks.extend(data)
        return bytes(chunks)

    def run(self):
        try:
            while True:
                line = self.reader.readline(100000)
                if not line:
                    raise EOFError()
                if not line.endswith(b"\n"):
                    raise ValueError("Invalid proxy frame")
                kind, identifier, encoded = json.loads(line)
                data = base64.b64decode(encoded, validate=True)
                if len(data) > 65536:
                    raise ValueError("Invalid proxy frame")
                if kind == OPEN and self.remote_port:
                    try:
                        connection = socket.create_connection(("127.0.0.1", self.remote_port), timeout=10)
                        connection.settimeout(None)
                        self.attach(identifier, connection)
                    except OSError:
                        self.send(CLOSE, identifier)
                elif kind == DATA and identifier in self.sockets:
                    try: self.sockets[identifier].sendall(data)
                    except OSError: self.close(identifier)
                elif kind == CLOSE:
                    self.close(identifier)
        except EOFError:
            pass
        finally:
            for identifier in list(self.sockets):
                self.close(identifier)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["stdio", "listen"])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--command-file", type=Path)
    args = parser.parse_args()
    if args.mode == "stdio":
        Multiplexer(sys.stdin.buffer, sys.stdout.buffer, args.port).run()
        return
    command = json.loads(args.command_file.read_text())
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr)
    tunnel = Multiplexer(process.stdout, process.stdin)
    reader = threading.Thread(target=tunnel.run, daemon=True)
    reader.start()
    try:
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", args.port))
            listener.listen(16)
            listener.settimeout(1)
            identifier = 0
            while reader.is_alive() and process.poll() is None:
                try: connection, _ = listener.accept()
                except socket.timeout: continue
                identifier += 1
                tunnel.send(OPEN, identifier)
                tunnel.attach(identifier, connection)
    finally:
        process.terminate()


if __name__ == "__main__":
    main()
