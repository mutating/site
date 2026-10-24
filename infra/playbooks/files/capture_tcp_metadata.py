"""Capture TCP headers only, without printing or saving packet payloads."""

import argparse
import json
import select
import socket
import struct
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--max-packets", type=int, default=80)
    args = parser.parse_args()
    source = None if args.source == 'any' else socket.inet_aton(args.source)
    deadline = time.monotonic() + args.seconds
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3)) as capture:
        print(json.dumps({"capture": "started", "source": args.source, "time": time.time()}), flush=True)
        count = 0
        while time.monotonic() < deadline and count < args.max_packets:
            ready, _, _ = select.select([capture], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            data, link = capture.recvfrom(65535)
            if len(data) < 54 or data[12:14] != b"\x08\x00":
                continue
            ip = 14
            if data[ip + 9] != socket.IPPROTO_TCP:
                continue
            src, dst = data[ip + 12:ip + 16], data[ip + 16:ip + 20]
            if source is not None and source not in (src, dst):
                continue
            tcp = ip + (data[ip] & 15) * 4
            if len(data) < tcp + 20:
                continue
            sport, dport, seq, ack = struct.unpack_from("!HHII", data, tcp)
            if 80 not in (sport, dport) and 443 not in (sport, dport):
                continue
            total_length = struct.unpack_from("!H", data, ip + 2)[0]
            payload_length = total_length - (tcp - ip) - (data[tcp + 12] >> 4) * 4
            flags = data[tcp + 13]
            options = data[tcp + 20:tcp + (data[tcp + 12] >> 4) * 4]
            tcp_options = {}
            index = 0
            while index < len(options):
                kind = options[index]
                if kind == 0:
                    break
                if kind == 1:
                    index += 1
                    continue
                if index + 1 >= len(options) or options[index + 1] < 2:
                    break
                length = options[index + 1]
                value = options[index + 2:index + length]
                if kind == 2 and len(value) == 2:
                    tcp_options['mss'] = struct.unpack('!H', value)[0]
                if kind == 8 and len(value) == 8:
                    tcp_options['timestamps'] = struct.unpack('!II', value)
                index += length
            print(json.dumps({
                "time": round(time.time(), 6), "interface": link[0],
                "direction": "out" if link[2] == 4 else "in",
                "source": socket.inet_ntoa(src), "sport": sport,
                "destination": socket.inet_ntoa(dst), "dport": dport,
                "seq": seq, "ack": ack, "flags": flags,
                "payload_bytes": payload_length,
                "ttl": data[ip + 8],
                "ip_id": struct.unpack_from('!H', data, ip + 4)[0],
                "window": struct.unpack_from('!H', data, tcp + 14)[0],
                "tcp_options": tcp_options,
            }), flush=True)
            count += 1
        print(json.dumps({"capture": "finished", "packets": count, "time": time.time()}), flush=True)


if __name__ == "__main__":
    main()
