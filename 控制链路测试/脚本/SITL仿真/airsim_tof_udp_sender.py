#!/usr/bin/env python3
"""Read AirSim ToF and send validated samples to the Nano over UDP.

This Windows-side adapter only reads AirSim RPC and emits sensor samples. It
does not open PX4 serial, arm, change mode, send setpoints, or command motors.
"""

import argparse
import json
import math
import socket
import sys
import time
from pathlib import Path


DEFAULT_PYTHON_CLIENT = Path(
    r"H:\VisualDrone\AirSim\AirSim-1.8.1-windows\PythonClient"
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--airsim-host", default="127.0.0.1")
    parser.add_argument("--nano-host", default="10.128.80.1")
    parser.add_argument("--nano-port", type=int, default=14660)
    parser.add_argument("--vehicle", default="PX4")
    parser.add_argument("--sensor", default="DownwardDistance")
    parser.add_argument("--rate", type=float, default=20.0)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--saturation-margin", type=float, default=0.05)
    parser.add_argument(
        "--python-client", type=Path, default=DEFAULT_PYTHON_CLIENT
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.rate <= 0 or args.duration <= 0:
        parser.error("--rate and --duration must be greater than zero")
    if not 1 <= args.nano_port <= 65535:
        parser.error("--nano-port must be in 1..65535")
    if args.saturation_margin < 0:
        parser.error("--saturation-margin must be non-negative")
    return args


def main():
    args = parse_args()
    if args.dry_run:
        print("DRY RUN OK")
        print(f"destination={args.nano_host}:{args.nano_port}")
        print("allowed output: versioned ToF samples over UDP only")
        print("forbidden output: MAVLink, arming, mode, setpoint, actuator")
        return 0

    client_path = args.python_client.resolve()
    if not client_path.is_dir():
        raise SystemExit(f"AirSim PythonClient not found: {client_path}")
    sys.path.insert(0, str(client_path))
    try:
        import airsim
    except Exception as exc:
        raise SystemExit(f"Cannot import AirSim PythonClient: {exc}") from exc

    client = airsim.MultirotorClient(ip=args.airsim_host)
    client.confirmConnection()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    destination = (args.nano_host, args.nano_port)
    period = 1.0 / args.rate
    deadline = time.monotonic() + args.duration
    next_sample = time.monotonic()
    sequence = 0
    sent = 0
    rejected = 0
    last_report = time.monotonic()

    print(
        f"sending AirSim {args.vehicle}/{args.sensor} to "
        f"{args.nano_host}:{args.nano_port} at {args.rate:.1f} Hz",
        flush=True,
    )
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now < next_sample:
                time.sleep(min(0.01, next_sample - now))
                continue
            next_sample += period

            data = client.getDistanceSensorData(args.sensor, args.vehicle)
            distance = float(data.distance)
            minimum = float(data.min_distance)
            maximum = float(data.max_distance)
            valid = (
                int(data.time_stamp) > 0
                and math.isfinite(distance)
                and math.isfinite(minimum)
                and math.isfinite(maximum)
                and maximum > minimum >= 0.0
                and minimum <= distance < maximum - args.saturation_margin
            )
            if not valid:
                rejected += 1
                continue

            packet = {
                "protocol": "airsim-tof-v1",
                "sequence": sequence,
                "source_timestamp_ns": int(data.time_stamp),
                "sent_monotonic_ns": time.monotonic_ns(),
                "distance_m": distance,
                "min_distance_m": minimum,
                "max_distance_m": maximum,
                "orientation": 25,
                "sensor_type": 0,
                "covariance_cm2": 1,
            }
            payload = json.dumps(packet, separators=(",", ":")).encode("ascii")
            sock.sendto(payload, destination)
            sequence = (sequence + 1) & 0xFFFFFFFF
            sent += 1

            if now - last_report >= 1.0:
                print(
                    f"status sent={sent} rejected={rejected} "
                    f"distance_m={distance:.3f}",
                    flush=True,
                )
                last_report = now
    finally:
        sock.close()

    print(f"summary sent={sent} rejected={rejected}")
    if sent == 0:
        print("ERROR no valid ToF sample was sent", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
