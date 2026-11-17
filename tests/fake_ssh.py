#!/usr/bin/env python3
"""Stand-in for ssh in connect tests: the 'remote' host is this computer.

`fake_ssh.py [-o OPT]... HOST COMMAND` runs COMMAND with /bin/sh, passing stdin.
`fake_ssh.py -N ... -L 127.0.0.1:LP:127.0.0.1:RP HOST` forwards TCP LP -> RP.
Every invocation is appended to $FAKE_SSH_LOG so tests can check the options.
"""
import json
import os
import socket
import subprocess
import sys
import threading


def forward(local, target):
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', local))
    server.listen(16)

    def pipe(source, sink):
        try:
            while data := source.recv(65536):
                sink.sendall(data)
        except OSError:
            pass
        finally:
            for item in (source, sink):
                try:
                    item.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    while True:
        client, _ = server.accept()
        upstream = socket.create_connection(('127.0.0.1', target))
        threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
        threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()


def main(argv):
    with open(os.environ['FAKE_SSH_LOG'], 'a') as log:
        log.write(json.dumps(argv) + '\n')
    options, rest, spec, tunnel = [], list(argv), None, False
    while rest and rest[0].startswith('-'):
        flag = rest.pop(0)
        if flag == '-N':
            tunnel = True
        elif flag in ('-o', '-L', '-J'):
            value = rest.pop(0)
            options.append(value)
            if flag == '-L':
                spec = value
    host, command = rest[0], rest[1:]
    if tunnel:
        _, local, _, remote = spec.split(':')
        forward(int(local), int(remote))
        return 0
    return subprocess.run(['/bin/sh', '-c', ' '.join(command)]).returncode


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
