"""Read-only TCP/TLS comparisons, invoked by diagnose-public-access.yml."""

import argparse
import concurrent.futures
import ctypes
import json
import socket
import ssl
import sys
import time


class DarwinTCPInfo(ctypes.Structure):
    _fields_ = [
        ("state", ctypes.c_uint8),
        ("snd_wscale", ctypes.c_uint8),
        ("rcv_wscale", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8),
        ("options", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("rto", ctypes.c_uint32),
        ("maxseg", ctypes.c_uint32),
        ("snd_ssthresh", ctypes.c_uint32),
        ("snd_cwnd", ctypes.c_uint32),
        ("snd_wnd", ctypes.c_uint32),
        ("snd_sbbytes", ctypes.c_uint32),
        ("rcv_wnd", ctypes.c_uint32),
        ("rttcur", ctypes.c_uint32),
        ("srtt", ctypes.c_uint32),
        ("rttvar", ctypes.c_uint32),
        ("tfo", ctypes.c_uint32),
        ("txpackets", ctypes.c_uint64),
        ("txbytes", ctypes.c_uint64),
        ("txretransmitbytes", ctypes.c_uint64),
        ("rxpackets", ctypes.c_uint64),
        ("rxbytes", ctypes.c_uint64),
        ("rxoutoforderbytes", ctypes.c_uint64),
        ("txretransmitpackets", ctypes.c_uint64),
    ]


def tcp_info(sock):
    if sys.platform != "darwin":
        return {}
    try:
        info = DarwinTCPInfo.from_buffer_copy(
            sock.getsockopt(socket.IPPROTO_TCP, 0x106, ctypes.sizeof(DarwinTCPInfo))
        )
        return {
            key: getattr(info, key)
            for key in [
                "state", "maxseg", "snd_sbbytes", "txpackets", "txbytes",
                "txretransmitbytes", "rxpackets", "rxbytes", "txretransmitpackets",
            ]
        }
    except OSError as exc:
        return {"error": str(exc)}


def probe(test, domain, address):
    name, kind, mss, interface = test
    started = time.monotonic()
    out = {"test": name, "started_at": time.time()}
    if mss:
        out["requested_mss"] = mss
    if interface:
        out["interface"] = interface
    host = "example.com" if kind == "control" else address
    port = 80 if kind.startswith("http") else 443
    if kind.startswith("ssh"):
        port = 22
    conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    conn.settimeout(6)
    try:
        if mss:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, mss)
        if interface:
            conn.setsockopt(socket.IPPROTO_IP, 25, socket.if_nametoindex(interface))
        conn.connect((socket.gethostbyname(host), port))
        out["tcp_seconds"] = round(time.monotonic() - started, 3)
        out["mss"] = conn.getsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG)
        if "delayed" in name:
            time.sleep(1)
        if port == 443:
            context = ssl.create_default_context()
            if kind == "tls12":
                context.maximum_version = ssl.TLSVersion.TLSv1_2
            elif kind == "tls13":
                context.minimum_version = ssl.TLSVersion.TLSv1_3
            server_name = "example.com" if kind == "control" else domain
            if kind == "no_sni":
                server_name = None
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            # Keep a duplicate for TCP counters even if wrap_socket times out.
            observer = conn.dup()
            try:
                conn = context.wrap_socket(conn, server_hostname=server_name)
                out["tls"] = conn.version()
            finally:
                out["tcp_info_after_tls"] = tcp_info(observer)
                observer.close()
        if port == 22:
            if kind == "ssh_after":
                out["server_banner"] = conn.recv(256).split(b"\r\n")[0].decode(errors="replace")
                conn.sendall(b"SSH-2.0-connectivity-diagnostic\r\n")
                out["response"] = f"SSH key exchange bytes: {len(conn.recv(512))}"
            elif kind == "ssh_send":
                conn.sendall(b"SSH-2.0-connectivity-diagnostic\r\n")
        elif kind == "http_minimal":
            conn.sendall(b"GET / HTTP/1.0\r\n\r\n")
        else:
            host_header = address if kind == "http_ip" else domain
            if kind == "control":
                host_header = "example.com"
            conn.sendall(
                f"HEAD / HTTP/1.1\r\nHost: {host_header}\r\nConnection: close\r\n\r\n".encode()
            )
        if kind != "ssh_after":
            out["response"] = conn.recv(512).split(b"\r\n")[0].decode(errors="replace")
    except (OSError, ssl.SSLError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            out["tcp_info"] = tcp_info(conn)
        finally:
            conn.close()
    out["total_seconds"] = round(time.monotonic() - started, 3)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--physical-interface", default="")
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    address = socket.gethostbyname(args.address)
    tests = [
        ("http_default", "http", None, None),
        ("http_ip", "http_ip", None, None),
        ("http_minimal", "http_minimal", None, None),
        ("tls12_default", "tls12", None, None),
        ("tls13_default", "tls13", None, None),
        ("tls_without_sni", "no_sni", None, None),
        ("http_mss512", "http", 512, None),
        ("tls12_mss512", "tls12", 512, None),
        ("tls13_mss512", "tls13", 512, None),
        ("tls13_mss1000", "tls13", 1000, None),
        ("ssh_passive", "ssh_passive", None, None),
        ("ssh_send_banner", "ssh_send", None, None),
        ("ssh_after_banner_delayed", "ssh_after", None, None),
        ("http_delayed", "http", None, None),
        ("tls_delayed", "tls13", None, None),
        ("control_default", "control", None, None),
        ("control_mss512", "control", 512, None),
    ]
    if sys.platform == "darwin" and args.physical_interface:
        tests.extend([
            ("http_physical_interface", "http", None, args.physical_interface),
            ("tls_physical_interface", "tls13", None, args.physical_interface),
        ])
    if args.only:
        tests = [test for test in tests if args.only in test[0]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda test: probe(test, args.domain, address), tests))
    print(json.dumps({"address": address, "results": results}))


if __name__ == "__main__":
    main()
