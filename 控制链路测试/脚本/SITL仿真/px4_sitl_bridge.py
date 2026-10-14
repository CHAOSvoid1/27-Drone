#!/usr/bin/env python3
"""Bridge bounded Nano JSON commands to a localhost-only PX4 SITL instance.

Safety properties:
- PX4 destination is local loopback, or an address discovered directly from a
  named local WSL distro; arbitrary network destinations are not accepted.
- Receives SITL telemetry on port 14540 (loopback by default, all local
  interfaces only when the explicit WSL mode is selected).
- No serial support.
- Never sends arming, disarming, or flight-mode commands.
- Default invalid/stale handling produces continuous zero-velocity setpoints.
- The explicit ``--stale-action stop`` mode stops only the SITL setpoint stream,
  allowing PX4's Offboard-loss behavior to be tested in simulation.
"""

import argparse
import collections
import json
import math
import select
import socket
import struct
import subprocess
import time


COMMAND_SCHEMA = "uav_sitl_cmd_v1"
STATUS_SCHEMA = "uav_sitl_status_v1"
MAX_ABS_VELOCITY = 0.5

CRC_EXTRA = {
    0: 50,
    30: 39,
    32: 185,
    36: 222,
    74: 20,
    84: 143,
}

MSG_NAMES = {
    0: "HEARTBEAT",
    30: "ATTITUDE",
    32: "LOCAL_POSITION_NED",
    36: "SERVO_OUTPUT_RAW",
    74: "VFR_HUD",
}


def x25_accumulate(byte, crc):
    tmp = (byte ^ (crc & 0xFF)) & 0xFF
    tmp = (tmp ^ (tmp << 4)) & 0xFF
    return ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF


def mav_crc(data, extra):
    crc = 0xFFFF
    for byte in data:
        crc = x25_accumulate(byte, crc)
    return x25_accumulate(extra, crc)


def frame_v1(sequence, system_id, component_id, message_id, payload):
    header = struct.pack("<BBBBB", len(payload), sequence, system_id, component_id, message_id)
    crc = mav_crc(header + payload, CRC_EXTRA.get(message_id, 0))
    return b"\xFE" + header + payload + struct.pack("<H", crc)


def heartbeat_v1(sequence):
    payload = struct.pack("<IBBBBB", 0, 18, 8, 0, 3, 3)
    return frame_v1(sequence, 42, 191, 0, payload)


def velocity_setpoint_v1(sequence, time_boot_ms, vx, vy, vz):
    type_mask = 0x0DC7
    payload = struct.pack(
        "<I11fHBBB",
        time_boot_ms,
        0.0, 0.0, 0.0,
        vx, vy, vz,
        0.0, 0.0, 0.0,
        0.0, 0.0,
        type_mask,
        1, 1, 1,
    )
    return frame_v1(sequence, 42, 191, 84, payload)


def parse_mavlink_frames(buffer):
    frames = []
    while buffer:
        if buffer[0] not in (0xFE, 0xFD):
            del buffer[0]
            continue

        if buffer[0] == 0xFE:
            if len(buffer) < 8:
                break
            payload_len = buffer[1]
            frame_len = 6 + payload_len + 2
            if len(buffer) < frame_len:
                break
            raw = bytes(buffer[:frame_len])
            del buffer[:frame_len]
            frames.append({
                "version": 1,
                "seq": raw[2],
                "sysid": raw[3],
                "compid": raw[4],
                "msgid": raw[5],
                "payload": raw[6:6 + payload_len],
            })
            continue

        if len(buffer) < 12:
            break
        payload_len = buffer[1]
        signed = bool(buffer[2] & 0x01)
        frame_len = 10 + payload_len + 2 + (13 if signed else 0)
        if len(buffer) < frame_len:
            break
        raw = bytes(buffer[:frame_len])
        del buffer[:frame_len]
        msgid = raw[7] | (raw[8] << 8) | (raw[9] << 16)
        frames.append({
            "version": 2,
            "seq": raw[4],
            "sysid": raw[5],
            "compid": raw[6],
            "msgid": msgid,
            "payload": raw[10:10 + payload_len],
        })
    return frames


def finite_bounded(value):
    value = float(value)
    if not math.isfinite(value) or abs(value) > MAX_ABS_VELOCITY:
        raise ValueError("velocity is not finite or exceeds +/-0.5 m/s")
    return value


def sitl_endpoints(wsl_distro):
    """Return endpoints limited to local host or the selected local WSL VM."""
    if not wsl_distro:
        return ("127.0.0.1", 14580), ("127.0.0.1", 14540)

    try:
        output = subprocess.check_output(
            ["wsl.exe", "-d", wsl_distro, "--", "hostname", "-I"],
            text=True,
            timeout=10,
        )
        address = output.strip().split()[0]
        resolved = socket.inet_ntoa(socket.inet_aton(address))
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        raise RuntimeError(f"cannot discover WSL address for {wsl_distro!r}: {exc}") from exc

    # WSL2 is local to this computer, but uses a private VM address. Discovery
    # through wsl.exe prevents this option from becoming an arbitrary network
    # target selector.
    return (resolved, 14580), ("0.0.0.0", 14540)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=15000)
    parser.add_argument(
        "--wsl-distro",
        help="send only to the named local WSL distro's discovered SITL address",
    )
    parser.add_argument("--duration", type=float, default=0.0, help="0 runs until Ctrl+C")
    parser.add_argument("--command-timeout", type=float, default=0.5)
    parser.add_argument(
        "--stale-action",
        choices=("zero", "stop"),
        default="zero",
        help=(
            "zero keeps sending zero-velocity setpoints; stop suppresses setpoints "
            "after timeout so PX4 SITL can exercise Offboard-loss handling"
        ),
    )
    args = parser.parse_args()

    if not 0.1 <= args.command_timeout <= 2.0:
        parser.error("--command-timeout must be between 0.1 and 2.0 seconds")

    try:
        sitl_endpoint, sitl_bind = sitl_endpoints(args.wsl_distro)
    except RuntimeError as exc:
        parser.error(str(exc))

    command_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    command_socket.bind((args.listen_host, args.listen_port))
    command_socket.setblocking(False)

    sitl_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sitl_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sitl_socket.bind(sitl_bind)
    # Windows turns an ICMP "port unreachable" response into WinError 10054
    # on a later recvfrom() when SITL is not running yet. Keep the bridge alive
    # so PX4 can be started before or after this program.
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        sitl_socket.ioctl(socket.SIO_UDP_CONNRESET, False)
    sitl_socket.setblocking(False)

    start = time.monotonic()
    next_heartbeat = start
    next_setpoint = start
    next_report = start
    next_status = start
    tx_sequence = 0
    last_command_time = None
    last_command = (0.0, 0.0, 0.0)
    last_source = None
    command_packets = 0
    rejected_packets = 0
    telemetry_bytes = 0
    frame_counts = collections.Counter()
    mav_buffer = bytearray()
    local_ned = None
    servo_output = None

    print(f"Nano JSON listening on {args.listen_host}:{args.listen_port}")
    print(f"PX4 SITL telemetry {sitl_bind[0]}:{sitl_bind[1]}")
    print(f"PX4 SITL setpoints -> {sitl_endpoint[0]}:{sitl_endpoint[1]}")
    print(f"Stale Nano input action: {args.stale_action}")
    print("No serial, arming, disarming, or mode commands are implemented")

    try:
        while args.duration == 0 or time.monotonic() - start < args.duration:
            now = time.monotonic()
            readable, _, _ = select.select([command_socket, sitl_socket], [], [], 0.02)

            for sock in readable:
                try:
                    payload, source = sock.recvfrom(65535)
                except ConnectionResetError:
                    continue
                if sock is command_socket:
                    try:
                        message = json.loads(payload.decode("utf-8"))
                        if message.get("schema") != COMMAND_SCHEMA:
                            raise ValueError("wrong schema")
                        if not isinstance(message.get("valid"), bool):
                            raise ValueError("valid must be boolean")
                        vx = finite_bounded(message.get("vx", 0.0))
                        vy = finite_bounded(message.get("vy", 0.0))
                        vz = finite_bounded(message.get("vz", 0.0))
                    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                        rejected_packets += 1
                        continue
                    command_packets += 1
                    last_source = source
                    last_command_time = now
                    last_command = (vx, vy, vz) if message["valid"] else (0.0, 0.0, 0.0)
                else:
                    telemetry_bytes += len(payload)
                    mav_buffer.extend(payload)

            for frame in parse_mavlink_frames(mav_buffer):
                msgid = frame["msgid"]
                body = frame["payload"]
                frame_counts[msgid] += 1
                if msgid == 32 and len(body) >= 28:
                    _, x, y, z, vx, vy, vz = struct.unpack("<Iffffff", body[:28])
                    local_ned = [round(x, 3), round(y, 3), round(z, 3),
                                 round(vx, 3), round(vy, 3), round(vz, 3)]
                elif msgid == 36 and len(body) >= 21:
                    values = struct.unpack("<I8HB", body[:21])
                    servo_output = list(values[1:9])

            stale = last_command_time is None or now - last_command_time > args.command_timeout
            active_command = (0.0, 0.0, 0.0) if stale else last_command
            setpoint_stream_active = not (stale and args.stale_action == "stop")

            if now >= next_heartbeat:
                sitl_socket.sendto(heartbeat_v1(tx_sequence), sitl_endpoint)
                tx_sequence = (tx_sequence + 1) & 0xFF
                next_heartbeat += 1.0

            if now >= next_setpoint and setpoint_stream_active:
                boot_ms = int((now - start) * 1000) & 0xFFFFFFFF
                sitl_socket.sendto(
                    velocity_setpoint_v1(tx_sequence, boot_ms, *active_command),
                    sitl_endpoint,
                )
                tx_sequence = (tx_sequence + 1) & 0xFF
                next_setpoint = now + 0.1
            elif now >= next_setpoint:
                # Do not accumulate a backlog while the stream is intentionally
                # suppressed. A later valid Nano command resumes at 10 Hz.
                next_setpoint = now + 0.1

            if last_source and now >= next_status:
                age = -1.0 if last_command_time is None else now - last_command_time
                status = {
                    "schema": STATUS_SCHEMA,
                    "command_age_s": round(age, 3),
                    "stale": stale,
                    "setpoint_stream_active": setpoint_stream_active,
                    "active_velocity": list(active_command),
                    "local_ned": local_ned,
                    "servo_output_raw": servo_output,
                }
                command_socket.sendto(
                    json.dumps(status, separators=(",", ":")).encode("utf-8"),
                    last_source,
                )
                # Schedule from the actual send time.  If the command source starts
                # late, do not emit a burst of "catch-up" status datagrams.
                next_status = now + 1.0

            if now >= next_report:
                age = -1.0 if last_command_time is None else now - last_command_time
                top = ", ".join(
                    f"{MSG_NAMES.get(mid, mid)}:{count}"
                    for mid, count in frame_counts.most_common(5)
                )
                print(
                    f"t={now-start:5.1f}s cmd_age={age:5.2f}s stale={stale} "
                    f"stream={setpoint_stream_active} "
                    f"v={active_command} json={command_packets}/{rejected_packets} "
                    f"telemetry={telemetry_bytes}B msgs=[{top}]"
                )
                print(f"  local_ned={local_ned} servo_output_raw={servo_output}")
                next_report += 1.0
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        command_socket.close()
        sitl_socket.close()

    print("Summary")
    print(f"  Nano JSON accepted/rejected: {command_packets}/{rejected_packets}")
    print(f"  SITL telemetry bytes: {telemetry_bytes}")
    print(f"  Last local NED: {local_ned}")
    print(f"  Last simulated servo output: {servo_output}")


if __name__ == "__main__":
    main()
