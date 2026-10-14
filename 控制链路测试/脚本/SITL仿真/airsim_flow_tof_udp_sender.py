#!/usr/bin/env python3
"""Send analytical AirSim optical-flow and ToF samples to the Nano over UDP.

This Windows-side sensor adapter reads AirSim ground truth and a downward
distance sensor. It does not open PX4 serial or send control commands.
"""

import argparse
import collections
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
    parser.add_argument("--nano-port", type=int, default=14661)
    parser.add_argument("--vehicle", default="PX4")
    parser.add_argument("--distance-sensor", default="DownwardDistance")
    parser.add_argument("--rate", type=float, default=50.0)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--quality", type=int, default=255)
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
    if not 0 <= args.quality <= 255:
        parser.error("--quality must be in 0..255")
    if args.saturation_margin < 0:
        parser.error("--saturation-margin must be non-negative")
    return args


def world_to_body(vector, q):
    """Rotate an AirSim NED vector into the FRD vehicle body frame."""
    w, x, y, z = q.w_val, q.x_val, q.y_val, q.z_val
    r00 = 1 - 2 * (y * y + z * z)
    r01 = 2 * (x * y - z * w)
    r02 = 2 * (x * z + y * w)
    r10 = 2 * (x * y + z * w)
    r11 = 1 - 2 * (x * x + z * z)
    r12 = 2 * (y * z - x * w)
    r20 = 2 * (x * z - y * w)
    r21 = 2 * (y * z + x * w)
    r22 = 1 - 2 * (x * x + y * y)
    vx, vy, vz = vector.x_val, vector.y_val, vector.z_val
    return (
        r00 * vx + r10 * vy + r20 * vz,
        r01 * vx + r11 * vy + r21 * vz,
        r02 * vx + r12 * vy + r22 * vz,
    )


def main():
    args = parse_args()
    if args.dry_run:
        print("DRY RUN OK")
        print(f"destination={args.nano_host}:{args.nano_port}")
        print(f"requested_rate_hz={args.rate:.1f}")
        print("allowed output: versioned optical-flow/ToF samples over UDP only")
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
    start = time.monotonic()
    deadline = start + args.duration
    next_sample = start
    previous_sample = None
    sequence = sent = rejected = 0
    reject_reasons = collections.Counter()
    last_values = None
    last_report = start

    print(
        f"sending analytical flow + ToF for {args.vehicle} to "
        f"{args.nano_host}:{args.nano_port} at up to {args.rate:.1f} Hz",
        flush=True,
    )
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now < next_sample:
                time.sleep(min(0.005, next_sample - now))
                continue
            next_sample += period
            if now - next_sample > period:
                next_sample = now + period

            distance_data = client.getDistanceSensorData(
                args.distance_sensor, args.vehicle
            )
            kinematics = client.simGetGroundTruthKinematics(args.vehicle)
            sampled = time.monotonic()
            if previous_sample is None:
                previous_sample = sampled
                continue
            dt = sampled - previous_sample
            previous_sample = sampled

            distance = float(distance_data.distance)
            minimum = float(distance_data.min_distance)
            maximum = float(distance_data.max_distance)
            body_v = world_to_body(
                kinematics.linear_velocity, kinematics.orientation
            )
            omega = kinematics.angular_velocity
            angular_values = (omega.x_val, omega.y_val, omega.z_val)
            reasons = []
            if int(distance_data.time_stamp) <= 0:
                reasons.append("distance_timestamp")
            if not 0.005 <= dt <= 0.1:
                reasons.append("integration_time")
            if not all(math.isfinite(value) for value in (distance, minimum, maximum)):
                reasons.append("distance_nonfinite")
            elif not maximum > minimum >= 0.0:
                reasons.append("distance_range")
            elif not minimum <= distance < maximum - args.saturation_margin:
                reasons.append("distance_saturated_or_out_of_range")
            if not all(math.isfinite(value) for value in body_v):
                reasons.append("linear_velocity")
            if not all(math.isfinite(value) for value in angular_values):
                reasons.append("angular_velocity")
            last_values = (dt, distance, minimum, maximum, body_v, angular_values)
            if reasons:
                rejected += 1
                reject_reasons.update(reasons)
                if now - last_report >= 1.0:
                    print(
                        f"status sent={sent} rejected={rejected} "
                        f"reasons={dict(sorted(reject_reasons.items()))} "
                        f"dt_ms={dt * 1000:.2f} distance_m={distance:.4f} "
                        f"range_m=[{minimum:.4f},{maximum:.4f}]",
                        flush=True,
                    )
                    last_report = now
                continue

            # MAVLink OPTICAL_FLOW_RAD convention for a downward, body-aligned
            # sensor: +Y translation gives -X flow and +X gives +Y flow.
            gyro_x = float(omega.x_val) * dt
            gyro_y = float(omega.y_val) * dt
            gyro_z = float(omega.z_val) * dt
            integrated_x = gyro_x - body_v[1] / distance * dt
            integrated_y = gyro_y + body_v[0] / distance * dt
            packet = {
                "protocol": "airsim-flow-tof-v1",
                "sequence": sequence,
                "sent_monotonic_ns": time.monotonic_ns(),
                "integration_time_us": int(round(dt * 1e6)),
                "integrated_x_rad": integrated_x,
                "integrated_y_rad": integrated_y,
                "integrated_gyro_rad": [gyro_x, gyro_y, gyro_z],
                "quality": args.quality,
                "distance_m": distance,
                "min_distance_m": minimum,
                "max_distance_m": maximum,
                "orientation": 25,
                "sensor_type": 0,
                "covariance_cm2": 1,
            }
            sock.sendto(
                json.dumps(packet, separators=(",", ":")).encode("ascii"),
                destination,
            )
            sequence = (sequence + 1) & 0xFFFFFFFF
            sent += 1

            if now - last_report >= 1.0:
                print(
                    f"status sent={sent} rejected={rejected} "
                    f"dt_ms={dt * 1000:.1f} distance_m={distance:.3f} "
                    f"body_v=({body_v[0]:.3f},{body_v[1]:.3f}) "
                    f"flow_rad=({integrated_x:.5f},{integrated_y:.5f})",
                    flush=True,
                )
                last_report = now
    finally:
        sock.close()

    elapsed = time.monotonic() - start
    print(
        f"summary sent={sent} rejected={rejected} "
        f"achieved_rate_hz={sent / elapsed:.1f} "
        f"reject_reasons={dict(sorted(reject_reasons.items()))}"
    )
    if last_values is not None and sent == 0:
        dt, distance, minimum, maximum, body_v, angular_values = last_values
        print(
            "last_rejected_sample "
            f"dt_ms={dt * 1000:.3f} distance_m={distance:.6f} "
            f"range_m=[{minimum:.6f},{maximum:.6f}] "
            f"body_v=({body_v[0]:.6f},{body_v[1]:.6f},{body_v[2]:.6f}) "
            f"omega=({angular_values[0]:.6f},{angular_values[1]:.6f},"
            f"{angular_values[2]:.6f})"
        )
    return 0 if sent > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
