"""Compare TTL-limited TCP probes without changing routes or VPN settings."""

import argparse
import concurrent.futures
import json
import select
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time

from diagnose_transport import tcp_info


def icmp_listener(sock, stopped, observations):
    while not stopped.is_set():
        ready, _, _ = select.select([sock], [], [], 0.1)
        if not ready:
            continue
        data, peer = sock.recvfrom(4096)
        offset = (data[0] & 15) * 4 if data and data[0] >> 4 == 4 else 0
        if len(data) < offset + 8:
            continue
        kind, code = data[offset], data[offset + 1]
        result = {'router': peer[0], 'type': kind, 'code': code, 'time': time.time()}
        quoted = offset + 8
        if kind in (3, 11) and len(data) >= quoted + 28 and data[quoted] >> 4 == 4:
            inner_tcp = quoted + (data[quoted] & 15) * 4
            result.update({
                'destination': socket.inet_ntoa(data[quoted + 16:quoted + 20]),
                'protocol': data[quoted + 9], 'quoted_ttl': data[quoted + 8],
                'sport': struct.unpack_from('!H', data, inner_tcp)[0],
                'dport': struct.unpack_from('!H', data, inner_tcp + 2)[0],
                # Darwin returns the embedded IPv4 length in host byte order.
                'quoted_ip_bytes': struct.unpack_from('=H' if sys.platform == 'darwin' else '!H', data, quoted + 2)[0],
                'quoted_tcp_seq': struct.unpack_from('!I', data, inner_tcp + 4)[0],
            })
            if len(data) >= inner_tcp + 14:
                result['quoted_tcp_flags'] = data[inner_tcp + 13]
                result['quoted_payload_bytes'] = result['quoted_ip_bytes'] - (inner_tcp - quoted) - (data[inner_tcp + 12] >> 4) * 4
        observations.append(result)


def probe(address, phase, ttl, port, payload):
    out = {'phase': phase, 'ttl': ttl, 'port': port, 'started_at': time.time()}
    started = time.monotonic()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conn:
        conn.settimeout(1.3)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.bind(('', 0))
        out['local_port'] = conn.getsockname()[1]
        try:
            if phase == 'syn':
                conn.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            conn.connect((address, port))
            out['connected'] = True
            out['local_port'] = conn.getsockname()[1]
            out['tcp_seconds'] = round(time.monotonic() - started, 3)
            if phase == 'payload':
                conn.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
                conn.sendall(payload)
                conn.settimeout(0.55)
                try:
                    out['response_bytes'] = len(conn.recv(128))
                except OSError as exc:
                    out['receive_error'] = str(exc)
            out['tcp_info'] = tcp_info(conn)
        except OSError as exc:
            out['error'] = str(exc)
        finally:
            # End only this diagnostic connection rather than leaving TCP retries.
            try:
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            except OSError:
                pass
    out['total_seconds'] = round(time.monotonic() - started, 3)
    return out


def flow_boundary(address, index, port, payload):
    out = {'phase': 'fixed_flow', 'index': index, 'port': port, 'started_at': time.time(), 'sends': []}
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conn:
        conn.settimeout(2)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            conn.connect((address, port))
            out['local_port'] = conn.getsockname()[1]
            for ttl in [8, 9, 10, 11, 12, 14]:
                conn.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
                conn.sendall(payload)
                out['sends'].append({'ttl': ttl, 'time': time.time(), 'bytes': len(payload)})
                time.sleep(0.35)
            out['tcp_info'] = tcp_info(conn)
        except OSError as exc:
            out['error'] = str(exc)
    return out


def padded_probe(address, no_push, segments):
    out = {'phase': 'padded_http', 'no_push': no_push, 'segments': segments, 'started_at': time.time()}
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conn:
        conn.settimeout(2)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            conn.connect((address, 80))
            out['local_port'] = conn.getsockname()[1]
            mss = conn.getsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG)
            out['mss'] = mss
            if no_push:
                conn.setsockopt(socket.IPPROTO_TCP, getattr(socket, 'TCP_NOPUSH', 4), 1)
            prefix = b'HEAD / HTTP/1.1\r\nHost: mutating.tech\r\nX-Padding: '
            suffix = b'\r\nConnection: close\r\n\r\n'
            request = prefix + b'a' * (mss * segments - len(prefix) - len(suffix)) + suffix
            out['request_bytes'] = len(request)
            conn.sendall(request)
            out['response'] = conn.recv(256).split(b'\r\n')[0].decode(errors='replace')
        except OSError as exc:
            out['error'] = str(exc)
        finally:
            out['tcp_info'] = tcp_info(conn)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--address', default='mutating.tech')
    parser.add_argument('--port', type=int, default=80)
    parser.add_argument('--trace', action='store_true')
    parser.add_argument('--dense', action='store_true')
    parser.add_argument('--padding', action='store_true')
    parser.add_argument('--flow-boundary', action='store_true')
    args = parser.parse_args()
    address = socket.gethostbyname(args.address)
    payload = b'GET / HTTP/1.0\r\n\r\n'
    if args.port == 443:
        context = ssl.create_default_context()
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        output = ssl.MemoryBIO()
        handshake = context.wrap_bio(ssl.MemoryBIO(), output, server_side=False, server_hostname='mutating.tech')
        try:
            handshake.do_handshake()
        except ssl.SSLWantReadError:
            payload = output.read()
    if args.trace:
        def trace(protocol):
            command = ['/usr/sbin/traceroute', '-n', '-e', '-P', protocol, '-p', str(args.port), '-m', '24', '-q', '1', '-w', '1', address]
            result = subprocess.run(command, capture_output=True, text=True, timeout=45)
            return {'protocol': protocol, 'stdout': result.stdout, 'stderr': result.stderr, 'rc': result.returncode}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            print(json.dumps(list(pool.map(trace, ['tcp', 'icmp', 'udp']))))
        return
    observations = []
    stopped = threading.Event()
    icmp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    listener = threading.Thread(target=icmp_listener, args=(icmp, stopped, observations), daemon=True)
    listener.start()
    cases = [('syn', ttl) for ttl in [1, 2, 4, 8, 16, 32, 64]]
    cases += [('payload', ttl) for ttl in [1, 2, 4, 8, 12, 16, 20, 24, 32, 48, 64, 255]]
    if args.dense:
        cases = [(phase, ttl) for ttl in range(8, 17) for phase in ['syn', 'payload'] for repeat in range(2)]
    if args.flow_boundary:
        cases = [('syn', ttl) for ttl in range(9, 15) for repeat in range(2)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda case: probe(address, *case, args.port, payload), cases))
    if args.flow_boundary:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            results.extend(pool.map(lambda index: flow_boundary(address, index, args.port, payload), range(3)))
    if args.padding:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results.extend(pool.map(lambda case: padded_probe(address, *case), [(False, 1), (True, 1), (False, 2), (True, 2)]))
    time.sleep(0.2)
    stopped.set()
    listener.join(timeout=1)
    icmp.close()
    print(json.dumps({'address': address, 'results': results, 'icmp': observations}))


if __name__ == '__main__':
    main()
