#!/usr/bin/env python3
"""Read-only inspection of AirSim's raw OpticalFlow image.

The script does not arm, change mode, send a setpoint, open PX4 serial, or
publish MAVLink. It reports raw image-channel statistics together with AirSim
ground-truth motion so the flow encoding can be calibrated before conversion
to MAVLink OPTICAL_FLOW_RAD.
"""

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np


DEFAULT_PYTHON_CLIENT = Path(
    r"H:\VisualDrone\AirSim\AirSim-1.8.1-windows\PythonClient"
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--vehicle", default="PX4")
    parser.add_argument("--camera", default="DownwardFlow")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--distance-sensor", default="DownwardDistance")
    parser.add_argument("--flow-fov-deg", type=float, default=42.0)
    parser.add_argument(
        "--camera-yaw-deg",
        type=float,
        default=None,
        help=(
            "temporarily set the downward camera yaw for axis calibration; "
            "the original pose is restored before exit"
        ),
    )
    parser.add_argument(
        "--python-client", type=Path, default=DEFAULT_PYTHON_CLIENT
    )
    args = parser.parse_args()
    if args.samples <= 0 or args.interval < 0 or not 0 < args.flow_fov_deg < 180:
        parser.error(
            "--samples must be positive, --interval non-negative, and "
            "--flow-fov-deg in (0, 180)"
        )
    return args


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


def channel_summary(channel):
    values = channel.astype(np.float64)
    return (
        float(np.min(values)),
        float(np.percentile(values, 10)),
        float(np.median(values)),
        float(np.percentile(values, 90)),
        float(np.max(values)),
        float(np.std(values)),
    )


def signed_channel_summary(channel):
    values = channel.astype(np.uint8, copy=False).view(np.int8).astype(np.float64)
    return (
        float(np.min(values)),
        float(np.percentile(values, 10)),
        float(np.median(values)),
        float(np.mean(values)),
        float(np.percentile(values, 90)),
        float(np.max(values)),
        float(np.std(values)),
    )


def world_to_body(vector, q):
    """Rotate an AirSim NED vector into the vehicle body frame."""
    w, x, y, z = q.w_val, q.x_val, q.y_val, q.z_val
    rotation = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )
    ned = np.array([vector.x_val, vector.y_val, vector.z_val], dtype=np.float64)
    return rotation.T @ ned


def main():
    args = parse_args()
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
    camera_info = client.simGetCameraInfo(args.camera, args.vehicle)
    original_camera_pose = camera_info.pose
    if args.camera_yaw_deg is not None:
        calibration_pose = airsim.Pose(
            original_camera_pose.position,
            airsim.to_quaternion(
                math.radians(-90.0), 0.0, math.radians(args.camera_yaw_deg)
            ),
        )
        client.simSetCameraPose(args.camera, calibration_pose, args.vehicle)
        time.sleep(0.25)
        camera_info = client.simGetCameraInfo(args.camera, args.vehicle)
        print(
            f"temporary_camera_yaw_deg={args.camera_yaw_deg:.2f}; "
            "the original camera pose will be restored on exit",
            flush=True,
        )
    cr, cp, cy = quaternion_to_rpy_deg(camera_info.pose.orientation)
    print(
        f"camera={args.vehicle}/{args.camera} fov_deg={camera_info.fov:.2f} "
        f"rpy_deg=({cr:.2f},{cp:.2f},{cy:.2f})",
        flush=True,
    )

    previous_timestamp = None
    valid = 0
    focal_length_px = None
    measurements = []
    try:
      for index in range(1, args.samples + 1):
        request = airsim.ImageRequest(
            args.camera, airsim.ImageType.OpticalFlow, False, False
        )
        responses = client.simGetImages([request], args.vehicle)
        if len(responses) != 1:
            raise SystemExit(f"Expected one response, got {len(responses)}")
        response = responses[0]
        expected = int(response.width) * int(response.height) * 3
        raw = np.frombuffer(response.image_data_uint8, dtype=np.uint8)
        if response.width <= 0 or response.height <= 0 or raw.size != expected:
            raise SystemExit(
                "OPTICAL FLOW CHECK FAILED: "
                f"size={response.width}x{response.height} bytes={raw.size} "
                f"expected={expected} message={response.message!r}. "
                "Confirm the DownwardFlow camera and ImageType 8 settings."
            )

        image = raw.reshape(response.height, response.width, 3)
        if focal_length_px is None:
            focal_length_px = response.width / (
                2.0 * math.tan(math.radians(args.flow_fov_deg) / 2.0)
            )
            print(
                f"configured_flow_fov_deg={args.flow_fov_deg:.2f} "
                f"focal_length_px={focal_length_px:.3f}",
                flush=True,
            )
        border_y = max(1, response.height // 8)
        border_x = max(1, response.width // 8)
        roi = image[border_y:-border_y, border_x:-border_x, :]
        signed_summaries = [
            signed_channel_summary(roi[:, :, channel]) for channel in range(3)
        ]
        kinematics = client.simGetGroundTruthKinematics(args.vehicle)
        velocity = kinematics.linear_velocity
        angular = kinematics.angular_velocity
        roll_deg, pitch_deg, yaw_deg = quaternion_to_rpy_deg(
            kinematics.orientation
        )
        velocity_body = world_to_body(velocity, kinematics.orientation)
        distance_data = client.getDistanceSensorData(
            args.distance_sensor, args.vehicle
        )
        distance = float(distance_data.distance)
        dt_ms = (
            float("nan")
            if previous_timestamp is None
            else (int(response.time_stamp) - previous_timestamp) / 1e6
        )
        previous_timestamp = int(response.time_stamp)
        dt_s = 0.0 if not math.isfinite(dt_ms) else dt_ms / 1000.0
        expected_translation_px = (
            float("nan")
            if not math.isfinite(distance) or distance <= 0 or dt_s <= 0
            else math.hypot(velocity_body[0], velocity_body[1])
            / distance
            * dt_s
            * focal_length_px
        )
        valid += 1
        measurements.append(
            {
                "dt_ms": dt_ms,
                "distance": distance,
                "body_x": float(velocity_body[0]),
                "body_y": float(velocity_body[1]),
                "yaw": yaw_deg,
                "yaw_rate": float(angular.z_val),
                "expected_px": expected_translation_px,
                "s1_mean": signed_summaries[1][3],
                "s2_mean": signed_summaries[2][3],
            }
        )
        text = []
        for channel, values in enumerate(signed_summaries):
            lo, p10, median, mean, p90, hi, stddev = values
            text.append(
                f"s{channel}[min={lo:.0f},p10={p10:.0f},med={median:.0f},"
                f"mean={mean:.2f},p90={p90:.0f},max={hi:.0f},sd={stddev:.2f}]"
            )
        print(
            f"sample={index:02d} size={response.width}x{response.height} "
            f"dt_ms={dt_ms:.2f} "
            f"distance_m={distance:.3f} "
            f"vel_ned=({velocity.x_val:.3f},{velocity.y_val:.3f},"
            f"{velocity.z_val:.3f}) "
            f"vel_body=({velocity_body[0]:.3f},{velocity_body[1]:.3f},"
            f"{velocity_body[2]:.3f}) yaw_deg={yaw_deg:.2f} "
            f"omega=({angular.x_val:.3f},{angular.y_val:.3f},"
            f"{angular.z_val:.3f}) expected_translation_px="
            f"{expected_translation_px:.2f} "
            + " ".join(text),
            flush=True,
        )
        if index != args.samples:
            time.sleep(args.interval)
    finally:
        if args.camera_yaw_deg is not None:
            client.simSetCameraPose(args.camera, original_camera_pose, args.vehicle)
            print("original camera pose restored", flush=True)

    print(f"OPTICAL FLOW RAW CHECK OK: valid samples {valid}/{args.samples}")
    usable = [
        row
        for row in measurements[1:]
        if math.isfinite(row["dt_ms"])
        and row["dt_ms"] > 0
        and math.isfinite(row["expected_px"])
        and row["expected_px"] > 0
    ]
    if usable:
        def mean(key):
            return sum(row[key] for row in usable) / len(usable)

        dominant_channel = max(("s1_mean", "s2_mean"), key=lambda key: abs(mean(key)))
        encoded_mean = mean(dominant_channel)
        expected_mean = mean("expected_px")
        print(
            "CALIBRATION SUMMARY: "
            f"frames={len(usable)} mean_dt_ms={mean('dt_ms'):.2f} "
            f"mean_distance_m={mean('distance'):.3f} "
            f"mean_body_velocity_mps=({mean('body_x'):.3f},"
            f"{mean('body_y'):.3f}) "
            f"yaw_change_deg={usable[-1]['yaw'] - usable[0]['yaw']:.2f} "
            f"mean_yaw_rate_rad_s={mean('yaw_rate'):.4f} "
            f"dominant={dominant_channel} encoded_mean={encoded_mean:.3f} "
            f"pinhole_expected_px={expected_mean:.3f} "
            f"encoded_to_pinhole_ratio={encoded_mean / expected_mean:.3f}",
            flush=True,
        )
    print(
        "These are raw encoded image channels, not radians. "
        "Do not publish OPTICAL_FLOW_RAD until stationary and known-motion "
        "calibration establishes channel mapping, scale, sign, and timing."
    )


if __name__ == "__main__":
    main()
