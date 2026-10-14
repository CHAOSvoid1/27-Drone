#!/usr/bin/env python3
"""Bridge validated UDP optical-flow/ToF samples to PX4 MAVLink.

The only transmitted MAVLink messages are companion HEARTBEAT,
DISTANCE_SENSOR and OPTICAL_FLOW_RAD. Publication stops on stale UDP input or
PX4 heartbeat. No arming, mode, setpoint or actuator message is implemented.
"""

import argparse
import collections
import json
import math
import os
import signal
import socket
import sys
import time

try:
    import fcntl
except ImportError:
    fcntl = None


SOURCE_SYSTEM = 42
SOURCE_COMPONENT = 191
LOCK_PATH = "/var/lock/nano_tof_mavlink_bridge.lock"
mavlink = None
serial = None


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/ttyS1")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=14661)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--input-timeout", type=float, default=0.25)
    parser.add_argument("--heartbeat-timeout", type=float, default=3.0)
    parser.add_argument("--target-system", type=int, default=1)
    parser.add_argument("--target-component", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.duration <= 0 or args.input_timeout <= 0 or args.heartbeat_timeout <= 0:
        parser.error("duration and timeouts must be greater than zero")
    if not 1 <= args.listen_port <= 65535:
        parser.error("--listen-port must be in 1..65535")
    return args


class Bridge:
    def __init__(self, args):
        self.args = args
        self.port = self.link = self.sock = self.lock_fd = None
        self.running = True
        self.start = time.monotonic()
        self.last_px4_heartbeat = self.last_udp = None
        self.next_heartbeat = self.start
        self.next_report = self.start + 1.0
        self.last_sequence = None
        self.counts = collections.Counter()

    def log(self, message):
        print(f"[{time.monotonic() - self.start:8.3f}] {message}", flush=True)

    def acquire_lock(self):
        if fcntl is None:
            raise RuntimeError("flock is unavailable; run live mode on Linux")
        self.lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(self.lock_fd, 0)
        os.write(self.lock_fd, f"pid={os.getpid()}\n".encode("ascii"))

    def open_io(self):
        self.port = serial.Serial(
            self.args.device,
            self.args.baud,
            timeout=0,
            write_timeout=1,
            rtscts=False,
            xonxoff=False,
            dsrdtr=False,
            exclusive=True,
        )
        self.link = mavlink.MAVLink(
            self.port, srcSystem=SOURCE_SYSTEM, srcComponent=SOURCE_COMPONENT
        )
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.bind((self.args.listen_host, self.args.listen_port))
        self.log(
            f"serial={self.args.device}@{self.args.baud} "
            f"udp={self.args.listen_host}:{self.args.listen_port}"
        )

    def drain_serial(self, now):
        waiting = self.port.in_waiting
        if waiting <= 0:
            return
        for byte in self.port.read(waiting):
            try:
                msg = self.link.parse_char(bytes((byte,)))
            except Exception:
                self.counts["parse_error"] += 1
                continue
            if msg is None:
                continue
            self.counts[f"rx_{msg.get_type()}"] += 1
            if (
                msg.get_type() == "HEARTBEAT"
                and msg.get_srcSystem() == self.args.target_system
                and msg.get_srcComponent() == self.args.target_component
                and int(msg.autopilot) == mavlink.MAV_AUTOPILOT_PX4
            ):
                if self.last_px4_heartbeat is None:
                    self.log("identified PX4 heartbeat")
                self.last_px4_heartbeat = now

    def send_heartbeat(self, now):
        if now < self.next_heartbeat:
            return
        self.link.heartbeat_send(
            mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
            mavlink.MAV_AUTOPILOT_INVALID,
            0,
            0,
            mavlink.MAV_STATE_ACTIVE,
        )
        self.next_heartbeat = now + 1.0

    def decode_sample(self, payload):
        sample = json.loads(payload.decode("ascii"))
        if sample.get("protocol") != "airsim-flow-tof-v1":
            raise ValueError("unexpected protocol")
        sequence = int(sample["sequence"])
        integration_us = int(sample["integration_time_us"])
        flow_x = float(sample["integrated_x_rad"])
        flow_y = float(sample["integrated_y_rad"])
        gyro = tuple(float(value) for value in sample["integrated_gyro_rad"])
        quality = int(sample["quality"])
        distance = float(sample["distance_m"])
        minimum = float(sample["min_distance_m"])
        maximum = float(sample["max_distance_m"])
        orientation = int(sample["orientation"])
        sensor_type = int(sample["sensor_type"])
        covariance = int(sample["covariance_cm2"])
        if len(gyro) != 3:
            raise ValueError("integrated gyro must contain three values")
        if not (
            5000 <= integration_us <= 100000
            and all(math.isfinite(value) and abs(value) <= 1.0 for value in (flow_x, flow_y, *gyro))
            and 0 <= quality <= 255
            and math.isfinite(distance)
            and math.isfinite(minimum)
            and math.isfinite(maximum)
            and maximum > minimum >= 0.0
            and minimum <= distance <= maximum
            and 0 <= orientation <= 255
            and 0 <= sensor_type <= 255
            and 0 <= covariance <= 255
        ):
            raise ValueError("sample outside allowed range")
        if self.last_sequence is not None:
            delta = (sequence - self.last_sequence) & 0xFFFFFFFF
            if delta == 0 or delta > 0x7FFFFFFF:
                raise ValueError("duplicate or out-of-order sequence")
        self.last_sequence = sequence
        return (
            integration_us, flow_x, flow_y, gyro, quality, distance,
            minimum, maximum, orientation, sensor_type, covariance,
        )

    def receive_udp(self, now):
        while True:
            try:
                payload, _peer = self.sock.recvfrom(4096)
            except BlockingIOError:
                return
            self.counts["udp_rx"] += 1
            try:
                values = self.decode_sample(payload)
            except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
                self.counts["udp_rejected"] += 1
                continue
            self.last_udp = now
            if self.last_px4_heartbeat is None:
                self.counts["not_published_no_px4"] += 1
                continue
            if now - self.last_px4_heartbeat > self.args.heartbeat_timeout:
                self.counts["not_published_stale_px4"] += 1
                continue

            (
                integration_us, flow_x, flow_y, gyro, quality, distance,
                minimum, maximum, orientation, sensor_type, covariance,
            ) = values
            timestamp_us = int((now - self.start) * 1e6)
            self.link.optical_flow_rad_send(
                timestamp_us,
                0,
                integration_us,
                flow_x,
                flow_y,
                gyro[0],
                gyro[1],
                gyro[2],
                0,
                quality,
                0,
                distance,
            )
            self.link.distance_sensor_send(
                int((now - self.start) * 1000.0) & 0xFFFFFFFF,
                max(0, min(65535, int(round(minimum * 100.0)))),
                max(0, min(65535, int(round(maximum * 100.0)))),
                max(0, min(65535, int(round(distance * 100.0)))),
                sensor_type,
                0,
                orientation,
                covariance,
            )
            self.counts["optical_flow_tx"] += 1
            self.counts["distance_sensor_tx"] += 1

    def report(self, now):
        if now < self.next_report:
            return
        px4_age = "none" if self.last_px4_heartbeat is None else f"{now - self.last_px4_heartbeat:.2f}s"
        udp_age = "none" if self.last_udp is None else f"{now - self.last_udp:.2f}s"
        self.log(
            f"status px4_age={px4_age} udp_age={udp_age} "
            f"udp_rx={self.counts['udp_rx']} flow_tx={self.counts['optical_flow_tx']} "
            f"distance_tx={self.counts['distance_sensor_tx']} "
            f"rejected={self.counts['udp_rejected']}"
        )
        self.next_report = now + 1.0

    def run(self):
        self.acquire_lock()
        self.open_io()
        deadline = self.start + self.args.duration
        while self.running and time.monotonic() < deadline:
            now = time.monotonic()
            self.drain_serial(now)
            self.send_heartbeat(now)
            self.receive_udp(now)
            self.report(now)
            time.sleep(0.002)
        self.log(f"summary counts={dict(sorted(self.counts.items()))}")
        return 0 if self.counts["optical_flow_tx"] > 0 else 1

    def close(self):
        if self.sock is not None:
            self.sock.close()
        if self.port is not None:
            self.port.close()
        if self.lock_fd is not None:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)


def main():
    global mavlink, serial
    args = parse_args()
    if args.dry_run:
        print("DRY RUN OK")
        print(f"serial={args.device}@{args.baud}")
        print(f"udp={args.listen_host}:{args.listen_port}")
        print("allowed MAVLink TX: HEARTBEAT, DISTANCE_SENSOR, OPTICAL_FLOW_RAD")
        print("forbidden MAVLink TX: arming, mode, setpoint, actuator")
        return 0
    try:
        import serial as loaded_serial
        from pymavlink.dialects.v10 import common as loaded_mavlink
    except Exception as exc:
        print(f"ERROR dependency import failed: {exc}", file=sys.stderr)
        return 2
    serial = loaded_serial
    mavlink = loaded_mavlink
    bridge = Bridge(args)

    def stop(_signum, _frame):
        bridge.running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        return bridge.run()
    except Exception as exc:
        bridge.log(f"ERROR {type(exc).__name__}: {exc}")
        return 2
    finally:
        bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
