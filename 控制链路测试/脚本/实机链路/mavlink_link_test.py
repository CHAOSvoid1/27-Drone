#!/usr/bin/env python3
"""Minimal MAVLink v1 serial link test for LicheeRV Nano.

Uses only Python standard library and pyserial. It sends a 1 Hz companion
computer heartbeat, parses common PX4 messages, and can send bounded local-NED
velocity setpoints for disarmed bench verification. It never arms the vehicle
or requests a flight-mode change.
"""

import argparse
import collections
import signal
import struct
import time

import serial


MSG_NAMES = {
    0: "HEARTBEAT",
    1: "SYS_STATUS",
    2: "SYSTEM_TIME",
    4: "PING",
    26: "SCALED_IMU",
    29: "SCALED_PRESSURE",
    30: "ATTITUDE",
    31: "ATTITUDE_QUATERNION",
    32: "LOCAL_POSITION_NED",
    36: "SERVO_OUTPUT_RAW",
    42: "MISSION_CURRENT",
    65: "RC_CHANNELS",
    74: "VFR_HUD",
    76: "COMMAND_LONG",
    77: "COMMAND_ACK",
    84: "SET_POSITION_TARGET_LOCAL_NED",
    105: "HIGHRES_IMU",
    147: "BATTERY_STATUS",
    148: "AUTOPILOT_VERSION",
}

# MAVLink common.xml CRC extras for messages used by this test.
CRC_EXTRA = {
    0: 50,
    1: 124,
    2: 137,
    4: 237,
    26: 170,
    29: 115,
    30: 39,
    31: 246,
    32: 185,
    36: 222,
    42: 28,
    65: 118,
    74: 20,
    76: 152,
    77: 143,
    84: 143,
    105: 93,
    147: 154,
    148: 178,
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


def heartbeat_v1(sequence, system_id=42, component_id=191):
    # custom_mode, type=ONBOARD_CONTROLLER, autopilot=INVALID,
    # base_mode=0, system_status=STANDBY, mavlink_version=3
    payload = struct.pack("<IBBBBB", 0, 18, 8, 0, 3, 3)
    header = struct.pack("<BBBBB", len(payload), sequence, system_id,
                         component_id, 0)
    crc = mav_crc(header + payload, CRC_EXTRA[0])
    return b"\xFE" + header + payload + struct.pack("<H", crc)


def request_message_v1(sequence, message_id, system_id=42, component_id=191,
                       target_system=1, target_component=1):
    # MAV_CMD_REQUEST_MESSAGE=512. param1 is the requested MAVLink message ID.
    payload = struct.pack(
        "<7fHBBB",
        float(message_id), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        512, target_system, target_component, 0,
    )
    header = struct.pack("<BBBBB", len(payload), sequence, system_id,
                         component_id, 76)
    crc = mav_crc(header + payload, CRC_EXTRA[76])
    return b"\xFE" + header + payload + struct.pack("<H", crc)


def velocity_setpoint_v1(sequence, time_boot_ms, vx, vy, vz, system_id=42,
                         component_id=191, target_system=1,
                         target_component=1):
    # Use vx/vy/vz in MAV_FRAME_LOCAL_NED. Ignore position, acceleration, yaw
    # and yaw rate. This function does not request Offboard mode or arming.
    type_mask = 0x0DC7
    payload = struct.pack(
        "<I11fHBBB",
        time_boot_ms,
        0.0, 0.0, 0.0,       # x, y, z ignored
        vx, vy, vz,           # vx, vy, vz active
        0.0, 0.0, 0.0,       # acceleration ignored
        0.0, 0.0,            # yaw and yaw rate ignored
        type_mask,
        target_system,
        target_component,
        1,                    # MAV_FRAME_LOCAL_NED
    )
    header = struct.pack("<BBBBB", len(payload), sequence, system_id,
                         component_id, 84)
    crc = mav_crc(header + payload, CRC_EXTRA[84])
    return b"\xFE" + header + payload + struct.pack("<H", crc)


def parse_frames(buffer):
    frames = []
    while True:
        try:
            start = buffer.index(0xFE)
        except ValueError:
            buffer.clear()
            break
        if start:
            del buffer[:start]
        if len(buffer) < 8:
            break
        payload_len = buffer[1]
        frame_len = 6 + payload_len + 2
        if len(buffer) < frame_len:
            break
        frame = bytes(buffer[:frame_len])
        del buffer[:frame_len]
        frames.append(frame)
    return frames


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="/dev/ttyS1")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--duration", type=float, default=60.0,
                        help="seconds; 0 means run until Ctrl+C")
    parser.add_argument(
        "--request-autopilot-version",
        action="store_true",
        help="send one harmless MAV_CMD_REQUEST_MESSAGE for AUTOPILOT_VERSION",
    )
    parser.add_argument(
        "--zero-velocity-seconds",
        type=float,
        default=0.0,
        help="send 5 Hz zero-velocity setpoints for this many seconds; does not change mode or arm",
    )
    parser.add_argument(
        "--velocity-seconds",
        type=float,
        default=0.0,
        help="send a bounded 5 Hz local-NED velocity setpoint for this many seconds",
    )
    parser.add_argument("--vx", type=float, default=0.0,
                        help="north velocity in m/s, limited to +/-0.5")
    parser.add_argument("--vy", type=float, default=0.0,
                        help="east velocity in m/s, limited to +/-0.5")
    parser.add_argument("--vz", type=float, default=0.0,
                        help="down velocity in m/s, limited to +/-0.5")
    args = parser.parse_args()

    if args.zero_velocity_seconds > 0 and args.velocity_seconds > 0:
        parser.error("choose either --zero-velocity-seconds or --velocity-seconds")
    if args.velocity_seconds > 0 and max(abs(args.vx), abs(args.vy), abs(args.vz)) > 0.5:
        parser.error("--vx/--vy/--vz are limited to +/-0.5 m/s for bench verification")

    running = True

    def stop(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    port = serial.Serial(
        args.device,
        args.baud,
        timeout=0.1,
        write_timeout=1,
        rtscts=False,
        xonxoff=False,
        dsrdtr=False,
    )
    port.reset_input_buffer()

    buffer = bytearray()
    counts = collections.Counter()
    crc_errors = 0
    parsed_frames = 0
    received_bytes = 0
    sent_heartbeats = 0
    tx_sequence = 0
    remote_sequences = {}
    sequence_gaps = 0
    latest_attitude = None
    latest_local_position = None
    last_rx_time = None
    start = time.monotonic()
    next_heartbeat = start
    request_sent = False
    request_ack = None
    autopilot_version_received = False
    zero_velocity_sent = 0
    velocity_sent = 0
    setpoint_start = start + 3.0
    next_setpoint = setpoint_start
    next_report = start + 1.0

    print(f"Opening {args.device} at {args.baud} baud")
    print("Never arms or changes mode; optional bounded velocity setpoints start at t=3s")

    try:
        while running and (args.duration == 0 or time.monotonic() - start < args.duration):
            now = time.monotonic()
            if now >= next_heartbeat:
                port.write(heartbeat_v1(tx_sequence))
                port.flush()
                tx_sequence = (tx_sequence + 1) & 0xFF
                sent_heartbeats += 1
                next_heartbeat += 1.0

            if args.request_autopilot_version and not request_sent and now - start >= 3.0:
                port.write(request_message_v1(tx_sequence, 148))
                port.flush()
                tx_sequence = (tx_sequence + 1) & 0xFF
                request_sent = True
                print("Sent MAV_CMD_REQUEST_MESSAGE for AUTOPILOT_VERSION (message 148)")

            setpoint_seconds = max(args.zero_velocity_seconds, args.velocity_seconds)
            if (setpoint_seconds > 0
                    and setpoint_start <= now < setpoint_start + setpoint_seconds
                    and now >= next_setpoint):
                boot_ms = int((now - start) * 1000) & 0xFFFFFFFF
                vx = args.vx if args.velocity_seconds > 0 else 0.0
                vy = args.vy if args.velocity_seconds > 0 else 0.0
                vz = args.vz if args.velocity_seconds > 0 else 0.0
                port.write(velocity_setpoint_v1(tx_sequence, boot_ms, vx, vy, vz))
                port.flush()
                tx_sequence = (tx_sequence + 1) & 0xFF
                if args.velocity_seconds > 0:
                    velocity_sent += 1
                else:
                    zero_velocity_sent += 1
                next_setpoint += 0.2

            chunk = port.read(port.in_waiting or 1)
            if chunk:
                buffer.extend(chunk)
                received_bytes += len(chunk)
                last_rx_time = now

            for frame in parse_frames(buffer):
                payload_len, seq, sysid, compid, msgid = frame[1:6]
                payload = frame[6:6 + payload_len]
                expected_crc = struct.unpack_from("<H", frame, 6 + payload_len)[0]
                extra = CRC_EXTRA.get(msgid)
                if extra is not None and mav_crc(frame[1:6 + payload_len], extra) != expected_crc:
                    crc_errors += 1
                    continue

                parsed_frames += 1
                counts[msgid] += 1
                key = (sysid, compid)
                if key in remote_sequences:
                    gap = (seq - remote_sequences[key] - 1) & 0xFF
                    if gap < 128:
                        sequence_gaps += gap
                remote_sequences[key] = seq

                if msgid == 30 and len(payload) >= 28:
                    _, roll, pitch, yaw, *_ = struct.unpack("<Iffffff", payload[:28])
                    latest_attitude = (roll, pitch, yaw)
                elif msgid == 32 and len(payload) >= 28:
                    _, x, y, z, vx, vy, vz = struct.unpack("<Iffffff", payload[:28])
                    latest_local_position = (x, y, z, vx, vy, vz)
                elif msgid == 77 and len(payload) >= 3:
                    command, result = struct.unpack_from("<HB", payload)
                    if command == 512:
                        request_ack = result
                        print(f"Received COMMAND_ACK command=512 result={result}")
                elif msgid == 148:
                    autopilot_version_received = True
                    print(f"Received AUTOPILOT_VERSION payload_len={len(payload)}")

            if now >= next_report:
                age = "never" if last_rx_time is None else f"{now - last_rx_time:.2f}s"
                top = ", ".join(
                    f"{MSG_NAMES.get(mid, str(mid))}:{count}"
                    for mid, count in counts.most_common(6)
                )
                print(
                    f"t={now-start:5.1f}s rx={received_bytes}B frames={parsed_frames} "
                    f"crc_err={crc_errors} seq_gaps={sequence_gaps} last_rx={age} "
                    f"msgs=[{top}]"
                )
                if latest_attitude:
                    print("  attitude rad roll={:.3f} pitch={:.3f} yaw={:.3f}".format(*latest_attitude))
                if latest_local_position:
                    print("  local NED m/mps x={:.2f} y={:.2f} z={:.2f} vx={:.2f} vy={:.2f} vz={:.2f}".format(
                        *latest_local_position
                    ))
                next_report += 1.0
    finally:
        port.close()

    elapsed = max(time.monotonic() - start, 0.001)
    print("\nSummary")
    print(f"  elapsed: {elapsed:.1f}s")
    print(f"  heartbeats sent: {sent_heartbeats}")
    print(f"  received: {received_bytes} bytes, {parsed_frames} frames")
    print(f"  average receive rate: {received_bytes / elapsed:.1f} B/s")
    print(f"  CRC errors (known messages): {crc_errors}")
    print(f"  sequence gaps: {sequence_gaps}")
    if args.request_autopilot_version:
        print(f"  request sent: {request_sent}")
        print(f"  COMMAND_ACK result: {request_ack}")
        print(f"  AUTOPILOT_VERSION received: {autopilot_version_received}")
    if args.zero_velocity_seconds > 0:
        print(f"  zero-velocity setpoints sent: {zero_velocity_sent}")
    if args.velocity_seconds > 0:
        print(f"  velocity setpoints sent: {velocity_sent}")
        print(f"  commanded local-NED velocity: vx={args.vx:.3f} vy={args.vy:.3f} vz={args.vz:.3f} m/s")
    print("  messages:")
    for msgid, count in counts.most_common():
        print(f"    {msgid:3d} {MSG_NAMES.get(msgid, 'UNKNOWN')}: {count}")


if __name__ == "__main__":
    main()
