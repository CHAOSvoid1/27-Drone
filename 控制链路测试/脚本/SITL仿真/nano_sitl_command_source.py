#!/usr/bin/env python3
"""Send bounded mock motion commands from LicheeRV Nano to a PC SITL bridge.

This program does not speak MAVLink and cannot arm or control PX4 directly.
It sends a small JSON datagram to the PC bridge, which is restricted to a
localhost PX4 SITL instance.
"""

import argparse
import ipaddress
import json
import socket
import time


SCHEMA = "uav_sitl_cmd_v1"
MAX_ABS_VELOCITY = 0.5


def private_or_loopback(address):
    ip = ipaddress.ip_address(socket.gethostbyname(address))
    if not (ip.is_private or ip.is_loopback):
        raise ValueError("--host must resolve to a private or loopback IPv4 address")
    return str(ip)


def bounded(value):
    return max(-MAX_ABS_VELOCITY, min(MAX_ABS_VELOCITY, float(value)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="PC USB-NCM/private IPv4 address")
    parser.add_argument("--port", type=int, default=15000)
    parser.add_argument("--rate", type=float, default=10.0)
    parser.add_argument("--warmup-seconds", type=float, default=5.0)
    parser.add_argument("--move-seconds", type=float, default=5.0)
    parser.add_argument("--stop-seconds", type=float, default=10.0)
    parser.add_argument("--vx", type=float, default=0.25)
    parser.add_argument("--vy", type=float, default=0.0)
    parser.add_argument("--vz", type=float, default=0.0)
    args = parser.parse_args()

    if not 2.0 <= args.rate <= 20.0:
        parser.error("--rate must be between 2 and 20 Hz")
    if min(args.warmup_seconds, args.move_seconds, args.stop_seconds) < 0:
        parser.error("phase durations must be non-negative")
    if max(abs(args.vx), abs(args.vy), abs(args.vz)) > MAX_ABS_VELOCITY:
        parser.error("velocity components are limited to +/-0.5 m/s")

    host = private_or_loopback(args.host)
    destination = (host, args.port)
    period = 1.0 / args.rate
    total_duration = args.warmup_seconds + args.move_seconds + args.stop_seconds
    start = time.monotonic()
    next_send = start
    next_report = start
    sequence = 0
    sent = 0
    status_received = 0

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    # Windows reports an ICMP port-unreachable response as WinError 10054 on
    # recvfrom(). Keep the deterministic test source alive if the bridge starts
    # slightly later; Linux/Nano does not expose this socket option.
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        sock.ioctl(socket.SIO_UDP_CONNRESET, False)
    sock.setblocking(False)

    print(f"Sending {SCHEMA} to {host}:{args.port}")
    print("Profile: zero -> bounded velocity -> zero; no MAVLink, arming, or mode commands")

    try:
        while True:
            now = time.monotonic()
            elapsed = now - start
            if elapsed >= total_duration:
                break

            if elapsed < args.warmup_seconds:
                phase = "warmup_zero"
                vx = vy = vz = 0.0
            elif elapsed < args.warmup_seconds + args.move_seconds:
                phase = "move"
                vx, vy, vz = bounded(args.vx), bounded(args.vy), bounded(args.vz)
            else:
                phase = "stop_zero"
                vx = vy = vz = 0.0

            if now >= next_send:
                message = {
                    "schema": SCHEMA,
                    "seq": sequence,
                    "sender_monotonic_ns": time.monotonic_ns(),
                    "valid": True,
                    "phase": phase,
                    "vx": vx,
                    "vy": vy,
                    "vz": vz,
                }
                sock.sendto(json.dumps(message, separators=(",", ":")).encode("utf-8"), destination)
                sequence += 1
                sent += 1
                next_send += period

            while True:
                try:
                    payload, _ = sock.recvfrom(4096)
                except (BlockingIOError, ConnectionResetError):
                    break
                try:
                    status = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if status.get("schema") == "uav_sitl_status_v1":
                    status_received += 1
                    print(
                        "SITL status: age={:.3f}s local_ned={} servo={}".format(
                            status.get("command_age_s", -1.0),
                            status.get("local_ned"),
                            status.get("servo_output_raw"),
                        )
                    )

            if now >= next_report:
                print(
                    f"t={elapsed:5.1f}s phase={phase:11s} "
                    f"v=({vx:+.2f},{vy:+.2f},{vz:+.2f}) sent={sent} status={status_received}"
                )
                next_report += 1.0

            time.sleep(min(0.01, max(0.0, next_send - time.monotonic())))
    finally:
        invalid = {
            "schema": SCHEMA,
            "seq": sequence,
            "sender_monotonic_ns": time.monotonic_ns(),
            "valid": False,
            "phase": "exit",
            "vx": 0.0,
            "vy": 0.0,
            "vz": 0.0,
        }
        for _ in range(3):
            sock.sendto(json.dumps(invalid, separators=(",", ":")).encode("utf-8"), destination)
            time.sleep(0.02)
        sock.close()

    print(f"Summary: sent={sent}, SITL status messages received={status_received}")


if __name__ == "__main__":
    main()
