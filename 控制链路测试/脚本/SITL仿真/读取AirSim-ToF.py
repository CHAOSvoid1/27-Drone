#!/usr/bin/env python3
"""Read-only check for the AirSim downward distance sensor.

This script opens only the AirSim RPC API. It does not arm, change mode,
send a flight setpoint, or write to the PX4 serial port.
"""

import argparse
import math
import statistics
import sys
import time
from pathlib import Path


DEFAULT_PYTHON_CLIENT = Path(
    r"H:\VisualDrone\AirSim\AirSim-1.8.1-windows\PythonClient"
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--vehicle", default="PX4")
    parser.add_argument("--sensor", default="DownwardDistance")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument(
        "--saturation-margin",
        type=float,
        default=0.05,
        help="distance below reported max range treated as saturation (metres)",
    )
    parser.add_argument(
        "--python-client",
        type=Path,
        default=DEFAULT_PYTHON_CLIENT,
    )
    return parser.parse_args()


def quaternion_to_rpy_deg(q):
    sin_roll = 2.0 * (q.w_val * q.x_val + q.y_val * q.z_val)
    cos_roll = 1.0 - 2.0 * (q.x_val * q.x_val + q.y_val * q.y_val)
    roll = math.atan2(sin_roll, cos_roll)

    sin_pitch = 2.0 * (q.w_val * q.y_val - q.z_val * q.x_val)
    pitch = math.asin(max(-1.0, min(1.0, sin_pitch)))

    sin_yaw = 2.0 * (q.w_val * q.z_val + q.x_val * q.y_val)
    cos_yaw = 1.0 - 2.0 * (q.y_val * q.y_val + q.z_val * q.z_val)
    yaw = math.atan2(sin_yaw, cos_yaw)
    return tuple(math.degrees(value) for value in (roll, pitch, yaw))


def main():
    args = parse_args()
    if args.samples <= 0 or args.interval < 0 or args.saturation_margin < 0:
        raise SystemExit(
            "--samples must be positive; --interval and "
            "--saturation-margin must be non-negative"
        )

    client_path = args.python_client.resolve()
    if not client_path.is_dir():
        raise SystemExit(f"AirSim PythonClient not found: {client_path}")
    sys.path.insert(0, str(client_path))

    try:
        import airsim
    except Exception as exc:
        raise SystemExit(f"Cannot import AirSim PythonClient: {exc}") from exc

    client = airsim.MultirotorClient(ip=args.host)
    client.confirmConnection()

    valid = 0
    saturated = 0
    distances = []
    for index in range(1, args.samples + 1):
        data = client.getDistanceSensorData(args.sensor, args.vehicle)
        kinematics = client.simGetGroundTruthKinematics(args.vehicle)
        distance = float(data.distance)
        timestamp = int(data.time_stamp)
        is_valid = math.isfinite(distance) and distance >= 0.0 and timestamp > 0
        valid += int(is_valid)
        if is_valid:
            distances.append(distance)

        max_distance = float(data.max_distance)
        is_saturated = (
            is_valid
            and math.isfinite(max_distance)
            and max_distance > 0.0
            and distance >= max_distance - args.saturation_margin
        )
        saturated += int(is_saturated)

        if index == 1:
            sensor_roll, sensor_pitch, sensor_yaw = quaternion_to_rpy_deg(
                data.relative_pose.orientation
            )
            sensor_position = data.relative_pose.position
            print(
                "sensor "
                f"range_m=[{float(data.min_distance):.3f},{max_distance:.3f}] "
                "relative_position_m="
                f"({sensor_position.x_val:.3f},{sensor_position.y_val:.3f},"
                f"{sensor_position.z_val:.3f}) "
                "relative_rpy_deg="
                f"({sensor_roll:.2f},{sensor_pitch:.2f},{sensor_yaw:.2f})",
                flush=True,
            )

        q = kinematics.orientation
        sin_roll = 2.0 * (q.w_val * q.x_val + q.y_val * q.z_val)
        cos_roll = 1.0 - 2.0 * (q.x_val * q.x_val + q.y_val * q.y_val)
        roll_deg = math.degrees(math.atan2(sin_roll, cos_roll))
        sin_pitch = 2.0 * (q.w_val * q.y_val - q.z_val * q.x_val)
        pitch_deg = math.degrees(math.asin(max(-1.0, min(1.0, sin_pitch))))

        p = kinematics.position
        v = kinematics.linear_velocity
        print(
            f"sample={index:02d} distance_m={distance:.4f} "
            f"ned_z_m={p.z_val:.4f} "
            f"velocity_mps=({v.x_val:.3f},{v.y_val:.3f},{v.z_val:.3f}) "
            f"roll_deg={roll_deg:.2f} pitch_deg={pitch_deg:.2f} "
            f"timestamp_ns={timestamp} valid={is_valid}",
            flush=True,
        )
        if index != args.samples:
            time.sleep(args.interval)

    if valid != args.samples:
        raise SystemExit(
            f"TOF CHECK FAILED: valid samples {valid}/{args.samples}. "
            "Check sensor name and AirSim settings.json."
        )
    mean = statistics.fmean(distances)
    stddev = statistics.pstdev(distances)
    minimum = min(distances)
    maximum = max(distances)
    print(
        f"TOF STATS: mean_m={mean:.4f} stddev_m={stddev:.4f} "
        f"min_m={minimum:.4f} max_m={maximum:.4f} "
        f"span_m={maximum - minimum:.4f} "
        f"saturated={saturated}/{args.samples}"
    )
    if saturated * 5 >= args.samples * 4:
        raise SystemExit(
            "TOF GEOMETRY CHECK FAILED: at least 80% of samples are at the "
            "reported maximum range. The ray likely did not hit the ground; "
            "check sensor pose, map collision, and test location."
        )
    print(f"TOF CHECK OK: valid samples {valid}/{args.samples}")


if __name__ == "__main__":
    main()
