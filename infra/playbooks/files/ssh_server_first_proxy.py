"""Ansible SSH ProxyCommand: receive the server banner before sending data.

The SSH client still verifies the host key and handles authentication/encryption.
This relay does not interpret, store or print the SSH stream.
"""

import argparse
import os
import select
import socket
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("port", type=int)
    parser.add_argument("--interface", help="Bind an IPv4 connection to a macOS interface")
    args = parser.parse_args()
    if args.interface:
        if sys.platform != "darwin":
            parser.error("--interface currently requires macOS")
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.settimeout(15)
        # IP_BOUND_IF is defined as 25 in the macOS SDK, but Python omits it.
        conn.setsockopt(socket.IPPROTO_IP, 25, socket.if_nametoindex(args.interface))
        conn.connect((args.host, args.port))
    else:
        conn = socket.create_connection((args.host, args.port), timeout=15)
    with conn:
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
