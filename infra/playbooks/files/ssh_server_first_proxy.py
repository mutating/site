"""Ansible SSH ProxyCommand: receive the server banner before sending data.

The SSH client still verifies the host key and handles authentication/encryption.
This relay does not interpret, store or print the SSH stream.
"""

import os
import select
import socket
import sys


def main():
    with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=15) as conn:
        first = conn.recv(16384)
        if not first:
            raise ConnectionError("Server closed before its SSH banner")
        sys.stdout.buffer.write(first)
        sys.stdout.buffer.flush()
        conn.settimeout(None)
        inputs = [conn, sys.stdin.buffer]
        while inputs:
            readable, _, _ = select.select(inputs, [], [])
            for source in readable:
                if source is conn:
                    data = conn.recv(65536)
                    if not data:
                        return
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
                else:
                    data = os.read(sys.stdin.fileno(), 65536)
                    if not data:
                        inputs.remove(sys.stdin.buffer)
                        conn.shutdown(socket.SHUT_WR)
                    else:
                        conn.sendall(data)


if __name__ == "__main__":
    main()
