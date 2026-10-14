#!/usr/bin/env python3
"""Observe and validate the Nano <-> PX4 MAVLink serial link safely.

The program has one nonblocking message pump. It sends only:
  * a 1 Hz companion-computer HEARTBEAT; and
  * optionally MAV_CMD_REQUEST_MESSAGE for AUTOPILOT_VERSION.

It never arms, changes flight mode, sends a setpoint, or sends an actuator or
motor-test command.  It is therefore suitable for checking the event-loop,
identity, heartbeat, ACK and shutdown behaviour before any actuator test.
"""

import argparse
import collections
import os
import signal
import sys
import time

try:
    import fcntl
except ImportError:  # Allows offline --dry-run on Windows.
    fcntl = None

mavlink = None
serial = None

# Only the common dialect is required by this observer.  Setting it before
# importing pymavlink avoids loading the larger ArduPilotMega dialect on the
# 128 MiB Nano.
os.environ.setdefault("MAVLINK_DIALECT", "common")


SOURCE_SYSTEM = 42
SOURCE_COMPONENT = 191
HEARTBEAT_HZ = 1.0
REPORT_PERIOD = 1.0
STARTUP_TIMEOUT = 10.0
DEFAULT_HEARTBEAT_TIMEOUT = 3.0
LOCK_PATH = "/var/lock/mavlink_protocol_observer.lock"


class Observer:
    def __init__(self, args):
        self.args = args
        self.link = None
        self.port = None
        self.lock_fd = None
        self.running = True
        self.cleaned = False
        self.exit_code = 0
        self.start_time = time.monotonic()
        self.last_target_heartbeat = None
        self.next_tx_heartbeat = self.start_time
        self.next_report = self.start_time + REPORT_PERIOD
        self.request_deadline = None
        self.request_sent = False
        self.request_ack = None
        self.version_received = False
        self.counts = collections.Counter()

    def log(self, text):
        elapsed = time.monotonic() - self.start_time
        print(f"[{elapsed:8.3f}] {text}", flush=True)

    def acquire_lock(self):
        if fcntl is None:
            raise RuntimeError("flock is unavailable; run the live test on Linux")
        self.lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another observer instance is running") from exc
        os.ftruncate(self.lock_fd, 0)
        os.write(
            self.lock_fd,
            f"pid={os.getpid()} started_monotonic={self.start_time:.3f}\n".encode(),
        )
        os.fsync(self.lock_fd)

    def open_link(self):
        self.port = serial.Serial(
            self.args.device,
            self.args.baud,
            timeout=0,
            write_timeout=1,
            rtscts=False,
            xonxoff=False,
            dsrdtr=False,
        )
        self.link = mavlink.MAVLink(
            self.port,
            srcSystem=SOURCE_SYSTEM,
            srcComponent=SOURCE_COMPONENT,
        )
        self.log(f"opened {self.args.device} at {self.args.baud} baud")

    def is_target(self, msg):
        return (
            msg.get_srcSystem() == self.args.target_system
            and msg.get_srcComponent() == self.args.target_component
        )

    def handle_message(self, msg, now):
        msg_type = msg.get_type()
        self.counts[msg_type] += 1

        if msg_type == "HEARTBEAT" and self.is_target(msg):
            autopilot = int(msg.autopilot)
            if autopilot != mavlink.MAV_AUTOPILOT_PX4:
                self.log(f"ERROR target heartbeat is not PX4 (autopilot={autopilot})")
                self.exit_code = 1
                self.running = False
                return
            if self.last_target_heartbeat is None:
                self.log(
                    "identified PX4 "
                    f"sys={msg.get_srcSystem()} comp={msg.get_srcComponent()}"
                )
            self.last_target_heartbeat = now

        elif msg_type == "COMMAND_ACK" and self.is_target(msg):
            self.log(
                f"COMMAND_ACK command={int(msg.command)} result={int(msg.result)}"
            )
            if int(msg.command) == mavlink.MAV_CMD_REQUEST_MESSAGE:
                self.request_ack = int(msg.result)

        elif msg_type == "AUTOPILOT_VERSION" and self.is_target(msg):
            self.version_received = True
            self.log("received AUTOPILOT_VERSION")

    def drain_messages(self, now):
        waiting = self.port.in_waiting
        data = self.port.read(waiting if waiting > 0 else 1)
        for byte in data:
            msg = self.link.parse_char(bytes((byte,)))
            if msg is not None:
                self.handle_message(msg, now)

    def send_heartbeat_if_due(self, now):
        if now < self.next_tx_heartbeat:
            return
        self.link.heartbeat_send(
            mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
            mavlink.MAV_AUTOPILOT_INVALID,
            0,
            0,
            mavlink.MAV_STATE_ACTIVE,
        )
        self.next_tx_heartbeat = now + 1.0 / HEARTBEAT_HZ

    def send_version_request_if_due(self, now):
        if not self.args.request_autopilot_version or self.request_sent:
            return
        if self.last_target_heartbeat is None:
            return
        self.link.command_long_send(
            self.args.target_system,
            self.args.target_component,
            mavlink.MAV_CMD_REQUEST_MESSAGE,
            0,
            float(mavlink.MAVLINK_MSG_ID_AUTOPILOT_VERSION),
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        )
        self.request_sent = True
        self.request_deadline = now + self.args.ack_timeout
        self.log("sent harmless AUTOPILOT_VERSION request")

    def check_timeouts(self, now):
        if self.last_target_heartbeat is None:
            if now - self.start_time > STARTUP_TIMEOUT:
                self.log("ERROR expected PX4 heartbeat not received")
                self.exit_code = 1
                self.running = False
            return

        age = now - self.last_target_heartbeat
        if age > self.args.heartbeat_timeout:
            self.log(f"ERROR PX4 heartbeat timeout, age={age:.2f}s")
            self.exit_code = 1
            self.running = False
            return

        if (
            self.request_deadline is not None
            and now >= self.request_deadline
            and (self.request_ack is None or not self.version_received)
        ):
            self.log(
                "ERROR version request incomplete: "
                f"ack={self.request_ack} version={self.version_received}"
            )
            self.exit_code = 1
            self.running = False

    def report_if_due(self, now):
        if now < self.next_report:
            return
        age = (
            "none"
            if self.last_target_heartbeat is None
            else f"{now - self.last_target_heartbeat:.2f}s"
        )
        self.log(
            f"status heartbeat_age={age} "
            f"rx_heartbeat={self.counts['HEARTBEAT']} "
            f"rx_ack={self.counts['COMMAND_ACK']}"
        )
        self.next_report = now + REPORT_PERIOD

    def run(self):
        self.acquire_lock()
        self.open_link()
        end_time = self.start_time + self.args.duration

        while self.running and time.monotonic() < end_time:
            now = time.monotonic()
            self.drain_messages(now)
            self.send_heartbeat_if_due(now)
            self.send_version_request_if_due(now)
            self.check_timeouts(now)
            self.report_if_due(now)
            time.sleep(0.01)

        if self.running and self.last_target_heartbeat is None:
            self.log("ERROR run ended before PX4 identity was verified")
            self.exit_code = 1
        elif self.running and self.args.request_autopilot_version:
            if self.request_ack != mavlink.MAV_RESULT_ACCEPTED:
                self.log(f"ERROR request ACK result={self.request_ack}")
                self.exit_code = 1
            elif not self.version_received:
                self.log("ERROR AUTOPILOT_VERSION was not received")
                self.exit_code = 1

        self.log(
            "summary "
            f"exit={self.exit_code} counts={dict(sorted(self.counts.items()))}"
        )
        return self.exit_code

    def cleanup(self):
        if self.cleaned:
            return
        self.cleaned = True
        if self.port is not None:
            self.port.close()
            self.port = None
        if self.link is not None:
            self.link = None
        if self.lock_fd is not None:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
            self.lock_fd = None


def dry_run(args):
    """Offline validation: no serial device, lock, or MAVLink command is used."""
    print("DRY RUN OK")
    print(f"device={args.device} baud={args.baud}")
    print(f"target={args.target_system}/{args.target_component}")
    print("allowed TX: HEARTBEAT, optional MAV_CMD_REQUEST_MESSAGE(512)")
    print("forbidden TX: arming, mode change, setpoint, motor/actuator command")
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/ttyS1")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--target-system", type=int, default=1)
    parser.add_argument("--target-component", type=int, default=1)
    parser.add_argument("--heartbeat-timeout", type=float, default=3.0)
    parser.add_argument("--ack-timeout", type=float, default=5.0)
    parser.add_argument("--request-autopilot-version", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("--duration must be greater than zero")
    if args.heartbeat_timeout <= 0 or args.ack_timeout <= 0:
        parser.error("timeouts must be greater than zero")
    return args


def main():
    global mavlink, serial
    args = parse_args()
    if args.dry_run:
        return dry_run(args)

    try:
        import serial as loaded_serial
        from pymavlink.dialects.v10 import common as loaded_mavlink
    except Exception as exc:
        print(
            "ERROR: live mode could not import pyserial/common dialect: "
            f"{type(exc).__name__}: {exc}. Use --dry-run or continue with "
            "the dependency-free mavlink_link_test.py",
            file=sys.stderr,
        )
        return 2
    serial = loaded_serial
    mavlink = loaded_mavlink

    observer = Observer(args)

    def request_stop(_signum, _frame):
        observer.log("stop requested")
        observer.running = False
        if observer.exit_code == 0:
            observer.exit_code = 3

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        return observer.run()
    except Exception as exc:
        observer.log(f"ERROR {type(exc).__name__}: {exc}")
        return 2
    finally:
        observer.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
