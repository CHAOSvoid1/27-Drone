#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nano_motor_test.py — Nano 发起的单路执行器测试命令（纯 pyserial，命令 310）。

审批状态：已完成离线自检；允许按文档执行无 4S 探测。接 4S 的实物模式仍须
先取得无 4S 探测证据并满足脚本门禁。

运行时栈（关键点）：
  * 不导入 pymavlink，也不导入 mavutil。128 MiB 的 LicheeRV Nano 在导入
    mavutil 时进程会退出，本文件因此只使用标准库 + pyserial，并按
    mavlink_link_test.py 已实测通过的 MAVLink v1 帧格式手工组帧与解析。
  * 后端固定为 VEHICLE_CMD_ACTUATOR_TEST(310)。PX4 1.16.2源码中的
    Commander::handleCommandActuatorTest()明确实现该命令；实机也已确认209返回
    UNSUPPORTED，因此本文件不再发送209。

命令参数（310，PX4 1.16.2实际实现）：
    param1 = 归一化输出值，8%发送为0.08
    param2 = 超时秒；0表示释放该输出的测试控制
    param3 / param4 = 0
    param5 = MAVLink输出功能，电机1..4对应1..4
    param6 / param7 = 0

两种运行模式：
  * `--no-4s-probe`：无4S协议探测。ESC无电、不可能转动，只用于取得310的
    ACK、`actuator_test`发布参数、完整超时窗口及释放控制ACK。
    这是确认 `param2` 超时语义的手段，因此**不要求**
    `--firmware-semantics-verified`。
    退出码 5 表示探测事务完成，不含任何物理结论。
  * 实时模式（不带 `--no-4s-probe`）：接4S、无桨、单机固定下的实物测试，
    必须先有 `--firmware-semantics-verified` 与 `--preflight-ok`。

物理结论必须在动作之后由操作者现场确认：程序在事务结束后才提问并带时间戳
记录，不接受在动作之前传入的确认开关。三层结论（协议接受 / PX4内部状态 /
物理动作）互不替代。

只证明：Nano → MAVLink → PX4 电机测试接口 → ESC → 电机。
不证明：Offboard、位置/姿态控制器、动态控制分配、解锁与失联处置。
"""

import argparse
import contextlib
import datetime
import io
import json
import os
import signal
import struct
import sys
import time

try:
    import fcntl
except ImportError:  # 允许在 Windows 上做离线检查
    fcntl = None


# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
SOURCE_SYSTEM = 42
SOURCE_COMPONENT = 191

VEHICLE_CMD_ACTUATOR_TEST = 310
MAV_RESULT_ACCEPTED = 0

MAV_AUTOPILOT_PX4 = 12
MAV_TYPE_ONBOARD_CONTROLLER = 18
MAV_AUTOPILOT_INVALID = 8
MAV_STATE_ACTIVE = 4
MAV_MODE_FLAG_SAFETY_ARMED = 128

# 硬上限：命令行不能突破。若 QGC 实测的最低稳定输出更低，应下调常量并重新评审。
MAX_OUTPUT_PERCENT = 10.0
MAX_TEST_SECONDS = 2.0
# PX4 1.16.2 Commander会把0或大于3000 ms的超时强制设为3000 ms。
MAX_PX4_TIMEOUT_SECONDS = 3.0
ALLOWED_MOTORS = (1, 2, 3, 4)

HEARTBEAT_PERIOD = 1.0
LOOP_PERIOD = 0.02

LOCK_PATH = "/var/lock/nano_motor_test.lock"
ALIVE_PATH = "/var/run/nano_motor_test.alive"

EXIT_OK = 0             # 实时模式：协议接受且三项现场确认均为真
EXIT_FAULT = 1
EXIT_USAGE = 2
EXIT_ABORT = 3
EXIT_UNCONFIRMED = 4    # 协议接受但现场确认缺失或非真
EXIT_PROBE_DONE = 5     # 无4S探测事务完成，不含任何物理结论

# MAVLink common.xml 的 CRC extra（仅本文件用到的消息）
CRC_EXTRA = {
    0: 50,     # HEARTBEAT
    1: 124,    # SYS_STATUS
    76: 152,   # COMMAND_LONG
    77: 143,   # COMMAND_ACK
    147: 154,  # BATTERY_STATUS
}

# MAVLink v1 线路顺序说明（不是 XML 声明顺序）：
# 字段按字节数从大到小排序，同长度组内保持 XML 声明顺序。
# BATTERY_STATUS(147, 36 字节)：
#   current_consumed(int32, 0) energy_consumed(int32, 4) temperature(int16, 8)
#   voltages[10](uint16, 10) current_battery(int16, 30)
#   id(32) battery_function(33) type(34) battery_remaining(35)
# SYS_STATUS(1, 31 字节)：
#   sensors_present(0) sensors_enabled(4) sensors_health(8) load(uint16, 12)
#   voltage_battery(uint16, 14) current_battery(int16, 16) drop_rate_comm(18)
#   errors_comm(20) errors_count1..4(22/24/26/28) battery_remaining(30)


# --------------------------------------------------------------------------
# MAVLink v1 帧：与 mavlink_link_test.py 相同的格式
# --------------------------------------------------------------------------
def x25_accumulate(byte, crc):
    tmp = (byte ^ (crc & 0xFF)) & 0xFF
    tmp = (tmp ^ (tmp << 4)) & 0xFF
    return ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF


def mav_crc(data, extra):
    crc = 0xFFFF
    for byte in data:
        crc = x25_accumulate(byte, crc)
    return x25_accumulate(extra, crc)


def build_frame(msg_id, payload, sequence):
    header = struct.pack("<BBBBB", len(payload), sequence & 0xFF,
                         SOURCE_SYSTEM, SOURCE_COMPONENT, msg_id)
    crc = mav_crc(header + payload, CRC_EXTRA[msg_id])
    return b"\xFE" + header + payload + struct.pack("<H", crc)


def heartbeat_frame(sequence):
    # custom_mode=0, type=ONBOARD_CONTROLLER, autopilot=INVALID,
    # base_mode=0, system_status=ACTIVE, mavlink_version=3
    return build_frame(0, struct.pack("<IBBBBB", 0, MAV_TYPE_ONBOARD_CONTROLLER,
                                      MAV_AUTOPILOT_INVALID, 0,
                                      MAV_STATE_ACTIVE, 3), sequence)


def command_long_frame(sequence, command, params, target_system, target_component):
    if len(params) != 7:
        raise ValueError("COMMAND_LONG needs exactly 7 parameters")
    payload = struct.pack("<7fHBBB", *[float(p) for p in params],
                          command, target_system, target_component, 0)
    return build_frame(76, payload, sequence)


def parse_frames(buffer):
    """从缓冲区切出完整帧；不完整的数据留在 buffer 中。"""
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


def decode_frame(frame):
    """返回 (msgid, sysid, compid, payload)，CRC 错误返回 None。"""
    payload_len, _seq, sysid, compid, msgid = frame[1:6]
    payload = frame[6:6 + payload_len]
    expected = struct.unpack_from("<H", frame, 6 + payload_len)[0]
    extra = CRC_EXTRA.get(msgid)
    if extra is not None and mav_crc(frame[1:6 + payload_len], extra) != expected:
        return None
    return msgid, sysid, compid, payload


def decode_heartbeat(payload):
    custom_mode, mtype, autopilot, base_mode, _status, _ver = struct.unpack(
        "<IBBBBB", payload[:10])
    return {
        "custom_mode": custom_mode,
        "type": mtype,
        "autopilot": autopilot,
        "base_mode": base_mode,
    }


def decode_command_ack(payload):
    command, result = struct.unpack_from("<HB", payload, 0)
    return int(command), int(result)


def decode_battery_status(payload):
    """按 MAVLink v1 线路字段顺序解析 BATTERY_STATUS。

    v1 可能裁剪尾部零字节，因此按可用长度容错；长度不足时 valid=False。
    注意：PX4 未必发送逐电芯电压，常见情况是只在 voltages[0] 放总电压，
    因此本解析器只上报原始值，不用于判断电芯数量。
    """
    info = {
        "valid": False,
        "cells": 0,
        "voltages_sum_v": 0.0,
        "first_cell_v": 0.0,
        "current_a": -1.0,
        "remaining": -1,
        "id": -1,
        "payload_len": len(payload),
    }
    if len(payload) >= 30:
        voltages = struct.unpack_from("<10H", payload, 10)
    elif len(payload) >= 12:
        voltages = (struct.unpack_from("<H", payload, 10)[0],)
    else:
        return info
    cells = [v for v in voltages if 0 < v < 0xFFFE]
    info["cells"] = len(cells)
    info["voltages_sum_v"] = sum(cells) / 1000.0
    info["first_cell_v"] = voltages[0] / 1000.0 if voltages[0] < 0xFFFE else 0.0
    if len(payload) >= 32:
        info["current_a"] = struct.unpack_from("<h", payload, 30)[0] / 100.0
    if len(payload) >= 33:
        info["id"] = struct.unpack_from("<B", payload, 32)[0]
    if len(payload) >= 36:
        info["remaining"] = struct.unpack_from("<b", payload, 35)[0]
    info["valid"] = True
    return info


def decode_sys_status(payload):
    """按 MAVLink v1 线路字段顺序解析 SYS_STATUS。

    voltage_battery 是整机电池电压(mV)，由飞控直接给出，
    不依赖逐电芯电压，因此用作电池门禁的主判据。
    """
    info = {
        "valid": False,
        "voltage_v": 0.0,
        "current_a": -1.0,
        "remaining": -1,
        "payload_len": len(payload),
    }
    if len(payload) < 16:
        return info
    info["voltage_v"] = struct.unpack_from("<H", payload, 14)[0] / 1000.0
    if len(payload) >= 18:
        info["current_a"] = struct.unpack_from("<h", payload, 16)[0] / 100.0
    if len(payload) >= 31:
        info["remaining"] = struct.unpack_from("<b", payload, 30)[0]
    info["valid"] = True
    return info


def describe_result(result):
    return {
        0: "ACCEPTED",
        1: "TEMPORARILY_REJECTED",
        2: "DENIED",
        3: "UNSUPPORTED",
        4: "FAILED",
        5: "IN_PROGRESS",
        6: "CANCELLED",
    }.get(result, f"UNKNOWN({result})")


# --------------------------------------------------------------------------
# 310 的参数构造：唯一出口，保证单路、归一化输出与超时
# --------------------------------------------------------------------------
def motor_test_params(motor, percent, px4_timeout):
    if motor not in ALLOWED_MOTORS:
        raise ValueError(f"motor must be one of {ALLOWED_MOTORS}, got {motor}")
    if not (0 < percent <= MAX_OUTPUT_PERCENT):
        raise ValueError(f"percent must be >0 and <= {MAX_OUTPUT_PERCENT}")
    if not (0 < px4_timeout <= MAX_PX4_TIMEOUT_SECONDS):
        raise ValueError("px4 timeout out of range")
    return (float(percent) / 100.0,
            float(px4_timeout),
            0.0,
            0.0,
            float(motor),
            0.0,
            0.0)


def stop_params(motor):
    if motor not in ALLOWED_MOTORS:
        raise ValueError(f"motor must be one of {ALLOWED_MOTORS}, got {motor}")
    # param2=0使PX4发布ACTION_RELEASE_CONTROL；param5仍明确指定单个电机。
    return (0.0, 0.0, 0.0, 0.0, float(motor), 0.0, 0.0)


def prompt_yes_no(question):
    """交互式现场确认；空行不作否定，必须明确输入y或n。"""
    while True:
        try:
            answer = input(question).strip().lower()
        except (EOFError, OSError, KeyboardInterrupt):
            return None
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("请输入 y 或 n；空行不会自动判定。", file=sys.stderr)


class MotorTest:
    def __init__(self, args, clock=time.monotonic, sleeper=time.sleep,
                 ask=None):
        self.args = args
        self.clock = clock
        self.sleeper = sleeper
        self.ask = ask                 # 现场确认提问入口；离线场景可注入
        self.ask_injected = ask is not None
        self.mode = "probe" if args.no_4s_probe else "live"
        # 无4S探测时电池不存在，门禁自动跳过
        self.require_battery = (args.battery == "require"
                                and self.mode != "probe")
        self.attestations = {}
        self.attested_at = None
        self.attestation_prompted = False
        self.operator_wait_seconds = 0.0

        self.port = None
        self.lock_fd = None
        self.log_handle = None
        self.cleaned = False
        self.finalized = False

        self.running = True
        self.exit_code = EXIT_FAULT     # 未证明通过前保持失败
        self.abort_reason = None
        self.fault_reason = None

        self.t0 = self.clock()
        self.state = "IDLE"
        self.state_entered = self.t0
        self.hard_deadline = self.t0 + args.max_process_seconds

        self.tx_seq = 0
        self.buffer = bytearray()
        self.next_hb = self.t0
        self.next_report = self.t0 + 1.0
        self.alive_warned = False

        self.identity_ok = False
        self.last_hb = None
        self.hb_count = 0
        self.armed = None
        self.base_mode = None
        self.foreign_heartbeats = 0

        self.battery = None             # BATTERY_STATUS，仅用于记录
        self.sys_status = None          # SYS_STATUS，电池门禁的主判据
        self.battery_parse_ok = None    # None=未收到, True=解析成功, False=解析失败
        self.battery_ok = False

        # 单一事务：同一时刻只有一个命令在等待 ACK
        self.tx = None                  # {"label","params","sent_at","deadline","result"}
        self.tx_history = []
        self.abandoned = []             # 被废弃的事务，用于 ACK 归属判定
        self.ack_attribution_unreliable = False
        self.stray_acks = 0
        self.ambiguous_ack = False

        self.start_sent = False
        self.start_sent_at = None
        self.run_started = None
        self.px4_deadline = None
        self.px4_timeout_elapsed = False
        self.protocol_accepted = False
        self.stop_attempted = False
        self.stop_frame_sent = False
        self.stop_ack = None
        self.clear_sent = False

    # ------------------------------------------------------------- 日志/锁
    def log(self, text):
        now = self.clock()
        wall = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{now - self.t0:8.3f}][{wall}][{self.state}] {text}"
        print(line, flush=True)
        if self.log_handle is not None:
            self.log_handle.write(line + "\n")
            self.log_handle.flush()

    def set_state(self, name):
        if name != self.state:
            self.log(f"state {self.state} -> {name}")
            self.state = name
            self.state_entered = self.clock()

    def acquire_lock(self):
        if fcntl is None:
            raise RuntimeError("flock unavailable; live mode requires Linux")
        self.lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.lock_fd)
            self.lock_fd = None
            raise RuntimeError("another motor test instance is running") from exc
        os.ftruncate(self.lock_fd, 0)
        os.write(self.lock_fd, b"pid=%d argv=%s\n"
                 % (os.getpid(), " ".join(sys.argv).encode("utf-8", "replace")))
        os.fsync(self.lock_fd)
        self.log(f"acquired lock {LOCK_PATH} pid={os.getpid()}")

    def open_port(self, serial_module):
        self.port = serial_module.Serial(
            self.args.device,
            self.args.baud,
            timeout=0,
            write_timeout=1,
            rtscts=False,
            xonxoff=False,
            dsrdtr=False,
        )
        self.log(f"opened {self.args.device} at {self.args.baud} baud")

    # ----------------------------------------------------------- 收发
    def write_frame(self, frame):
        """唯一的串口写入出口（心跳与命令都经过这里）。"""
        try:
            self.port.write(frame)
            self.port.flush()
            return True
        except Exception as exc:
            self.log(f"ERROR write failed: {type(exc).__name__}: {exc}")
            self.fault(f"serial write failed in state {self.state}")
            return False

    def send_heartbeat(self, now):
        if now < self.next_hb:
            return
        if not self.write_frame(heartbeat_frame(self.tx_seq)):
            return
        self.tx_seq = (self.tx_seq + 1) & 0xFF
        self.next_hb = now + HEARTBEAT_PERIOD

    def send_command(self, label, params, now):
        """发送一条COMMAND_LONG(310)，并建立唯一事务。"""
        if self.tx is not None:
            self.log(f"ERROR refuse {label}: transaction {self.tx['label']} pending")
            return False
        frame = command_long_frame(self.tx_seq, VEHICLE_CMD_ACTUATOR_TEST, params,
                                   self.args.target_system,
                                   self.args.target_component)
        if not self.write_frame(frame):
            return False
        self.tx_seq = (self.tx_seq + 1) & 0xFF
        self.tx = {
            "label": label,
            "params": tuple(params),
            "sent_at": now,
            "deadline": now + self.args.ack_timeout,
            "result": None,
        }
        if label == "stop":
            self.stop_frame_sent = True
        self.tx_history.append(self.tx)
        self.log(f"TX {label} cmd=310 params={list(self.tx['params'])} "
                 f"timeout={self.args.px4_timeout}s")
        return True

    def pump(self, now):
        """唯一的串口读取入口。"""
        if self.port is None:
            return
        waiting = self.port.in_waiting
        data = self.port.read(waiting if waiting > 0 else 1)
        if not data:
            return
        self.buffer.extend(data)
        for frame in parse_frames(self.buffer):
            decoded = decode_frame(frame)
            if decoded is None:
                self.log("WARN dropped a frame with a bad CRC")
                continue
            self.on_message(decoded, now)

    def on_message(self, decoded, now):
        msgid, sysid, compid, payload = decoded
        if (sysid != self.args.target_system
                or compid != self.args.target_component):
            if msgid == 0:
                self.foreign_heartbeats += 1
            return

        if msgid == 0:          # HEARTBEAT
            hb = decode_heartbeat(payload)
            if hb["autopilot"] != MAV_AUTOPILOT_PX4:
                self.fault(f"target heartbeat is not PX4 "
                           f"(autopilot={hb['autopilot']})")
                return
            if not self.identity_ok:
                self.identity_ok = True
                self.log(f"identified PX4 sys={sysid} comp={compid} "
                         f"type={hb['type']}")
            self.hb_count += 1
            self.last_hb = now
            self.base_mode = hb["base_mode"]
            self.armed = bool(hb["base_mode"] & MAV_MODE_FLAG_SAFETY_ARMED)
            return

        if msgid == 77:         # COMMAND_ACK
            command, result = decode_command_ack(payload)
            if (self.tx is not None and self.tx["result"] is None
                    and command == VEHICLE_CMD_ACTUATOR_TEST):
                self.tx["result"] = result
                self.log(f"ACK tx={self.tx['label']} command={command} "
                         f"result={result} ({describe_result(result)})")
                return
            self.stray_acks += 1
            if command == VEHICLE_CMD_ACTUATOR_TEST:
                self.ambiguous_ack = True
                self.log(f"AMBIGUOUS ACK command={command} result={result} "
                         f"outside a transaction")
            else:
                self.log(f"ignored unrelated ACK command={command}")
            return

        if msgid == 147:        # BATTERY_STATUS
            info = decode_battery_status(payload)
            self.battery = info
            if not info["valid"]:
                self.battery_parse_ok = False
                self.log(f"WARN BATTERY_STATUS payload too short "
                         f"({info['payload_len']} bytes)")
            elif self.battery_parse_ok is None:
                self.battery_parse_ok = True
            return

        if msgid == 1:          # SYS_STATUS
            info = decode_sys_status(payload)
            self.sys_status = info
            if not info["valid"]:
                self.battery_parse_ok = False
                self.log(f"WARN SYS_STATUS payload too short "
                         f"({info['payload_len']} bytes)")
            elif self.battery_parse_ok is None:
                self.battery_parse_ok = True
            return

    def abandon_tx(self, reason):
        """明确废弃未完成事务。

        之后到达的同命令 ID ACK 无法可靠归属，必须标记为归属不确定。
        """
        if self.tx is None:
            return
        tx = self.tx
        self.tx = None
        self.ack_attribution_unreliable = True
        self.abandoned.append({
            "label": tx["label"],
            "at": round(tx["sent_at"] - self.t0, 3),
            "reason": reason,
        })
        self.log(f"ABANDON transaction {tx['label']} ({reason}); a later "
                 f"COMMAND_ACK(310) cannot be attributed reliably")

    def take_tx_result(self):
        if self.tx is None or self.tx["result"] is None:
            return None
        tx = self.tx
        self.tx = None
        return tx

    # --------------------------------------------------------- 健康与状态机
    def check_health(self, now):
        if now > self.hard_deadline:
            self.fault(f"hard process deadline {self.args.max_process_seconds}s")
            return
        if not self.identity_ok:
            if now - self.t0 > self.args.startup_timeout:
                self.fault("PX4 identity not verified")
            return
        if self.last_hb is None:
            return
        age = now - self.last_hb
        if age > self.args.heartbeat_timeout:
            self.fault(f"PX4 heartbeat timeout age={age:.2f}s")
            return
        if self.armed:
            self.fault("vehicle is ARMED")
            return
        if self.require_battery and self.sys_status is not None:
            ok, why = self.evaluate_battery()
            if not ok and self.start_sent:
                self.fault(f"battery out of range during test: {why}")

    def evaluate_battery(self):
        """门禁只用 SYS_STATUS 的整机电压。

        不用 BATTERY_STATUS 的电芯数量：PX4 未必逐电芯上报，
        常见情况是只在 voltages[0] 放总电压，逐电芯计数不可靠。
        """
        info = self.sys_status
        if info is None or not info["valid"]:
            return False, "no valid SYS_STATUS voltage"
        voltage = info["voltage_v"]
        if voltage <= 0:
            return False, f"SYS_STATUS voltage_battery is {voltage:.3f} V"
        low = self.args.expect_cells * self.args.min_cell_voltage
        high = self.args.expect_cells * self.args.max_cell_voltage
        if not (low <= voltage <= high):
            return False, (f"pack voltage {voltage:.2f} V outside "
                           f"[{low:.2f}, {high:.2f}]")
        return True, "ok"

    def step(self, now):
        if self.state == "CHECK":
            self.do_check(now)
        elif self.state == "CLEAR":
            self.do_clear(now)
        elif self.state == "QUIET":
            self.do_quiet(now)
        elif self.state == "SEND":
            self.do_send(now)
        elif self.state == "ACK":
            self.do_ack(now)
        elif self.state == "OBSERVE":
            self.do_observe(now)
        elif self.state == "STOP":
            self.do_stop(now)
        elif self.state == "VERIFY":
            self.do_verify(now)

    def do_check(self, now):
        if not self.identity_ok or self.last_hb is None or self.armed is None:
            return
        if self.armed:
            self.fault("vehicle is ARMED; refusing to send any motor command")
            return
        if now - self.last_hb > self.args.heartbeat_timeout:
            return  # 由 check_health 统一判定
        if self.hb_count < 2:
            return
        if self.require_battery:
            ok, why = self.evaluate_battery()
            if self.sys_status is None:
                if now - self.t0 > self.args.startup_timeout:
                    self.fault("SYS_STATUS not received; battery voltage unknown")
                return
            if not ok:
                self.fault(f"battery check failed: {why}")
                return
            self.battery_ok = True
        else:
            self.log("WARNING battery check skipped "
                     f"(mode={self.mode})")
        self.log("pre-send checks passed: disarmed, PX4 identity")
        self.set_state("CLEAR")

    def do_clear(self, now):
        if self.tx is None and not self.clear_sent:
            if not self.send_command("clear", stop_params(self.args.motor), now):
                self.fault("clear command could not be sent")
                return
            self.clear_sent = True
            return
        tx = self.take_tx_result()
        if tx is not None:
            if tx["result"] == MAV_RESULT_ACCEPTED:
                self.log("clear command accepted")
                self.set_state("QUIET")
            else:
                self.fault(f"clear command result={tx['result']} "
                           f"({describe_result(tx['result'])})")
            return
        if self.tx is not None and now >= self.tx["deadline"]:
            self.tx = None
            self.fault("clear ACK timeout; refusing to start")

    def do_quiet(self, now):
        if self.ambiguous_ack:
            self.fault("ambiguous COMMAND_ACK(310) before start; refusing")
            return
        if now - self.state_entered >= self.args.ack_settle:
            self.log(f"quiet window clean, stray_acks={self.stray_acks}")
            self.set_state("SEND")

    def do_send(self, now):
        if self.start_sent:
            return
        if not self.send_command("start",
                                 motor_test_params(self.args.motor,
                                                   self.args.pct,
                                                   self.args.px4_timeout), now):
            self.fault("start command could not be sent")
            return
        self.start_sent = True
        self.start_sent_at = now
        self.px4_deadline = now + self.args.px4_timeout
        self.set_state("ACK")

    def do_ack(self, now):
        tx = self.take_tx_result()
        if tx is not None:
            if tx["result"] == MAV_RESULT_ACCEPTED:
                self.protocol_accepted = True
                self.log("start command ACCEPTED (protocol level only)")
                self.set_state("OBSERVE")
            else:
                self.fault(f"start command result={tx['result']} "
                           f"({describe_result(tx['result'])}); no fallback to 310")
            return
        if self.tx is not None and now >= self.tx["deadline"]:
            self.tx = None
            self.fault("start ACK timeout")

    def do_observe(self, now):
        if self.run_started is None:
            self.run_started = now
            if self.mode == "probe":
                # 状态进入必须在发送任何停止命令之前取证
                self.attest("actuator_test_entered",
                            "PX4 内部 actuator_test 是否已进入？(y/N) ")
        if now - self.run_started >= self.args.secs:
            self.log("observe window finished")
            if self.mode == "probe":
                # 探测模式先完整等待param2超时窗口，再做释放控制清理
                self.set_state("VERIFY")
            else:
                self.set_state("STOP")

    def do_stop(self, now):
        if self.tx is None:
            if not self.stop_attempted:
                self.stop_attempted = True
                if not self.send_command("stop", stop_params(self.args.motor), now):
                    self.after_stop()
                return
            self.finalize()
            return
        tx = self.take_tx_result()
        if tx is not None:
            self.stop_ack = tx["result"]
            self.log(f"stop command ack result={tx['result']} "
                     f"({describe_result(tx['result'])}); attempted, "
                     f"not a physical confirmation")
            self.after_stop()
            return
        if self.tx is not None and now >= self.tx["deadline"]:
            self.log(f"stop ACK not confirmed within {self.args.ack_timeout:.2f}s")
            self.tx = None
            self.after_stop()

    def after_stop(self):
        if self.fault_reason is not None or self.abort_reason is not None:
            self.finalize()
        elif self.mode == "probe":
            # 探测模式的验证已在清理停止之前完成
            self.finalize()
        else:
            self.set_state("VERIFY")

    def do_verify(self, now):
        if self.px4_deadline is None:
            self.finalize()
            return
        if now >= self.px4_deadline + self.args.verify_margin:
            self.px4_timeout_elapsed = True
            if self.mode == "probe":
                self.log(f"PX4 timeout window elapsed "
                         f"({self.args.px4_timeout:.2f}s + "
                         f"{self.args.verify_margin:.2f}s) "
                         f"before the cleanup stop command")
                # actuator_test是一次性命令topic，混控器内部超时不会再发布一条
                # “已清除”消息。这里只记录已完整等待固件param2超时窗口，随后
                # 发送显式RELEASE_CONTROL清理；不能要求操作者从旧topic值推断状态。
                self.log("PX4 param2 timeout window completed; actuator_test "
                         "topic is not republished by the mixer on expiry")
                self.set_state("STOP")
            else:
                self.log(f"PX4 timeout window elapsed "
                         f"({self.args.px4_timeout:.2f}s + "
                         f"{self.args.verify_margin:.2f}s); explicit stop "
                         f"had already been sent")
                self.finalize()

    # ------------------------------------------------------------ 故障/收尾
    def fault(self, reason):
        if self.fault_reason is None:
            self.fault_reason = reason
            self.log(f"FAULT {reason}")
        if self.start_sent:
            # 只有在停止命令尚未发出时，才需要废弃当前事务腾出唯一事务槽位。
            # 停止命令发出后再废弃会丢掉它自己的事务，导致不等待停止 ACK。
            if not self.stop_attempted:
                self.abandon_tx(f"fault: {reason}")
            if self.state != "STOP":
                self.set_state("STOP")
        else:
            self.abandon_tx(f"fault before start: {reason}")
            self.finalize()

    def build_report(self):
        return {
            "script": "nano_motor_test.py",
            "backend": 310,
            "motor": self.args.motor,
            "requested_percent": self.args.pct,
            "observe_seconds": self.args.secs,
            "px4_command_timeout_s": self.args.px4_timeout,
            "device": self.args.device,
            "hard_caps": {
                "max_output_percent": MAX_OUTPUT_PERCENT,
                "max_test_seconds": MAX_TEST_SECONDS,
                "allowed_motors": list(ALLOWED_MOTORS),
            },
            "mode": self.mode,
            "semantics_verified": bool(self.args.firmware_semantics_verified),
            "protocol_accepted": self.protocol_accepted,
            "physical_motor_observed": self.attestations.get("motor_observed"),
            "physical_stop_observed": self.attestations.get("stop_observed"),
            "other_motors_stationary": self.attestations.get("others_stationary"),
            "actuator_test_observed_entered":
                self.attestations.get("actuator_test_entered"),
            "actuator_test_observed_cleared":
                self.attestations.get("actuator_test_cleared"),
            "operator_attested_at": self.attested_at,
            "attestation_prompted": self.attestation_prompted,
            "operator_wait_seconds": round(self.operator_wait_seconds, 3),
            "battery_verified": self.battery_ok,
            "sys_status_parse_valid": bool(self.sys_status is not None
                                           and self.sys_status["valid"]),
            "battery_status_parse_valid": bool(self.battery is not None
                                               and self.battery["valid"]),
            "battery_source": "sys_status" if self.sys_status is not None else "none",
            "battery_voltage_v": (None if self.sys_status is None
                                  else round(self.sys_status["voltage_v"], 3)),
            "battery_status_reported": (None if self.battery is None
                                        else {
                                            "cells": self.battery["cells"],
                                            "voltages_sum_v": round(
                                                self.battery["voltages_sum_v"], 3),
                                            "first_cell_v": round(
                                                self.battery["first_cell_v"], 3),
                                        }),
            "px4_timeout_window_elapsed": self.px4_timeout_elapsed,
            "stop_attempted": self.stop_attempted,
            "stop_frame_sent": self.stop_frame_sent,
            "stop_ack": self.stop_ack,
            "stop_ack_unambiguous": bool(self.stop_ack is not None
                                         and not self.ack_attribution_unreliable),
            "abandoned_transaction": (None if not self.abandoned
                                      else self.abandoned[-1]),
            "ack_attribution_unreliable": self.ack_attribution_unreliable,
            "transactions": [
                {
                    "label": tx["label"],
                    "params": list(tx["params"]),
                    "sent_at": round(tx["sent_at"] - self.t0, 3),
                    "result": tx["result"],
                }
                for tx in self.tx_history
            ],
            "heartbeats_rx": self.hb_count,
            "foreign_heartbeats": self.foreign_heartbeats,
            "stray_acks": self.stray_acks,
            "ambiguous_ack": self.ambiguous_ack,
            "fault_reason": self.fault_reason,
            "abort_reason": self.abort_reason,
            "final_state": self.state,
        }

    def interactive_ready(self):
        """是否具备提问条件：注入的提问函数，或真实的交互式终端。"""
        if self.ask is None:
            return False
        if self.ask_injected:
            return True
        try:
            return sys.stdin is not None and sys.stdin.isatty()
        except Exception:
            return False

    def attest(self, key, question):
        """就地提问并带时间戳记录；不能提问时记为未确认。"""
        if not self.interactive_ready():
            return
        self.attestation_prompted = True
        wait_started = self.clock()
        self.attestations[key] = self.ask(question)
        wait_elapsed = max(0.0, self.clock() - wait_started)
        # 人工切换到QGC取证的时间不属于程序失控。只延长进程总时限，
        # 不移动px4_deadline，飞控侧param2超时仍按真实时间计算。
        self.operator_wait_seconds += wait_elapsed
        self.hard_deadline += wait_elapsed
        self.attested_at = datetime.datetime.now().isoformat(timespec="seconds")
        self.log(f"attestation {key}={self.attestations[key]} "
                 f"operator_wait={wait_elapsed:.2f}s")

    def collect_physical_attestations(self):
        """实物模式的三项现场确认，只在动作结束后进行。"""
        if not self.start_sent:
            return
        if not self.interactive_ready():
            self.log("no interactive terminal; physical conclusions stay "
                     "unconfirmed and must be recorded in the test log")
            return
        self.attest("motor_observed",
                    f"电机 M{self.args.motor} 是否确实转动？(y/N) ")
        self.attest("stop_observed",
                    f"电机 M{self.args.motor} 是否已完全停止？(y/N) ")
        self.attest("others_stationary", "其余三路是否始终静止？(y/N) ")

    def verdict_from_attestations(self):
        """三层结论互不替代；物理结论只认动作后的现场确认。"""
        if self.fault_reason is not None:
            return EXIT_FAULT, "FAULT"
        if self.abort_reason is not None:
            return EXIT_ABORT, "ABORTED"
        if not self.protocol_accepted:
            return EXIT_FAULT, "FAULT"
        if self.mode == "probe":
            # 探测模式不产生任何物理结论
            if self.px4_timeout_elapsed and self.stop_frame_sent:
                return EXIT_PROBE_DONE, "PROBE_COMPLETED_NO_PHYSICAL_CLAIM"
            return EXIT_FAULT, "FAULT"
        live = (self.attestations.get("motor_observed") is True
                and self.attestations.get("stop_observed") is True
                and self.attestations.get("others_stationary") is True)
        if live:
            return EXIT_OK, "FULL_PASS"
        return EXIT_UNCONFIRMED, "PROTOCOL_ACCEPTED_NOT_ATTESTED"

    def finalize(self):
        if self.finalized:
            return
        self.finalized = True
        self.running = False

        if self.mode == "live":
            self.collect_physical_attestations()
        code, verdict = self.verdict_from_attestations()

        self.set_state("COMPLETE" if code in (EXIT_OK, EXIT_PROBE_DONE)
                       else "FAULT")
        self.exit_code = code
        report = self.build_report()
        report["exit_code"] = code
        report["verdict"] = verdict
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        self.log("report\n" + payload)
        if self.args.report_file:
            try:
                with open(self.args.report_file, "w", encoding="utf-8") as handle:
                    handle.write(payload + "\n")
            except OSError as exc:
                self.log(f"ERROR cannot write report: {exc}")
        self.log(f"exit={code} ({verdict})")

    def run(self):
        if self.args.log_file:
            self.log_handle = open(self.args.log_file, "a", encoding="utf-8")
        self.set_state("CHECK")
        while self.running:
            now = self.clock()
            self.pump(now)              # 先收，再判超时
            if not self.running:
                break
            self.check_health(now)
            if not self.running:
                break
            self.step(now)
            if not self.running:
                break
            self.send_heartbeat(now)
            self.write_alive(now)
            self.report_if_due(now)
            self.sleeper(LOOP_PERIOD)
        self.run_stop_tail()
        self.finalize()
        return self.exit_code

    def run_stop_tail(self):
        """主循环已结束但停止事务未完成时，用同一套收发补完一次停止。"""
        if self.port is None or not self.start_sent or self.finalized:
            return
        # 中止或故障时可能带着未完成事务进入尾流程；先废弃，才能发出停止命令。
        # 停止命令已经在等待 ACK 时不要废弃，否则会丢掉它自己的事务。
        if not self.stop_attempted:
            self.abandon_tx("stop tail entered with a pending transaction")
        if self.stop_attempted and self.tx is None:
            return
        self.log("stop tail: completing the stop transaction")
        self.set_state("STOP")
        deadline = self.clock() + self.args.stop_tail_seconds
        while not self.finalized and self.clock() < deadline:
            now = self.clock()
            self.pump(now)
            self.do_stop(now)
            self.send_heartbeat(now)
            self.sleeper(LOOP_PERIOD)

    def write_alive(self, now):
        """外部监督者据此判断本进程是否卡死；进程内不做自我看门狗。"""
        try:
            with open(self.args.alive_file, "w", encoding="utf-8") as handle:
                handle.write(f"pid={os.getpid()} mono={now:.3f}\n")
        except OSError as exc:
            if not self.alive_warned:
                self.alive_warned = True
                self.log(f"WARNING cannot write {self.args.alive_file}: {exc}")

    def report_if_due(self, now):
        if now < self.next_report:
            return
        age = "none" if self.last_hb is None else f"{now - self.last_hb:.2f}s"
        self.log(f"status hb_age={age} hb_rx={self.hb_count} armed={self.armed} "
                 f"stray_acks={self.stray_acks} "
                 f"tx={'none' if self.tx is None else self.tx['label']}")
        self.next_report = now + 1.0

    def cleanup(self):
        """幂等清理：只释放资源，绝不写串口。"""
        if self.cleaned:
            return
        self.cleaned = True
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None
        if self.port is not None:
            try:
                self.port.close()
            except Exception as exc:
                self.log(f"WARNING port close failed: {exc}")
            self.port = None
        if self.lock_fd is not None and fcntl is not None:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self.lock_fd)
                self.lock_fd = None


# --------------------------------------------------------------------------
# 离线自检：不打开串口，只验证组帧/解析/参数护栏与状态机故障路径
# --------------------------------------------------------------------------
def px4_frame(msg_id, payload, sequence=0, sysid=1, compid=1):
    """构造来自飞控的帧，仅用于离线自检。"""
    header = struct.pack("<BBBBB", len(payload), sequence & 0xFF, sysid,
                         compid, msg_id)
    crc = mav_crc(header + payload, CRC_EXTRA[msg_id])
    return b"\xFE" + header + payload + struct.pack("<H", crc)


def battery_status_vector(voltages, remaining=86, current_ca=128):
    """按线路顺序构造 BATTERY_STATUS(147)：ii h 10H h B B B b = 36 字节。"""
    cells = list(voltages) + [0] * (10 - len(voltages))
    return px4_frame(147, struct.pack("<iih10HhBBBb", 1000, 2000, 250,
                                      *cells, current_ca, 0, 0, 0, remaining))


def sys_status_vector(voltage_mv, remaining=86, current_ca=128):
    """按线路顺序构造 SYS_STATUS(1)：III H H h H H H H H H b = 31 字节。"""
    return px4_frame(1, struct.pack("<IIIHHhHHHHHHb", 0, 0, 0, 500,
                                    voltage_mv, current_ca, 0, 0, 0, 0, 0, 0,
                                    remaining))


class FakeClock:
    def __init__(self, step=0.05):
        self.t = 0.0
        self.step = step

    def __call__(self):
        return self.t

    def advance(self, _dt=0.0):
        self.t += self.step


class FakePort:
    """离线串口替身：按伪造时钟产生心跳/电池报文，并对命令自动回 ACK。"""

    def __init__(self, clock, hb_period=1.0, hb_stop_at=None,
                 ack_results=None, ack_delay=0.05, inject=None,
                 emit_sys_status=True, hb_stop_after_start=False):
        self.clock = clock
        self.hb_period = hb_period
        self.hb_stop_at = hb_stop_at
        self.ack_results = list(ack_results or [])
        self.ack_delay = ack_delay
        self.inject = list(inject or [])
        self.emit_sys_status = emit_sys_status
        self.hb_stop_after_start = hb_stop_after_start
        self.written = []
        self.commands = []          # 按发送顺序记录 (command, params)
        self.next_hb_at = 0.0
        self.sys_status_sent = False
        self.pending = []
        self.closed = 0

    @property
    def in_waiting(self):
        return 1

    def read(self, _n=1):
        now = self.clock()
        ready = [i for i, item in enumerate(self.pending) if item[0] <= now]
        if ready:
            i = min(ready, key=lambda k: self.pending[k][0])
            return self.pending.pop(i)[1]
        ready = [i for i, item in enumerate(self.inject) if item[0] <= now]
        if ready:
            i = min(ready, key=lambda k: self.inject[k][0])
            return self.inject.pop(i)[1]
        if self.hb_stop_at is not None and now >= self.hb_stop_at:
            return b""
        if now >= self.next_hb_at:
            self.next_hb_at = now + self.hb_period
            if self.emit_sys_status and not self.sys_status_sent:
                self.sys_status_sent = True
                self.pending.append((now, sys_status_vector(16226)))
            return px4_frame(0, struct.pack("<IBBBBB", 0, 2, 12, 0, 4, 3))
        return b""

    def write(self, data):
        data = bytes(data)
        self.written.append(data)
        decoded = decode_frame(data)
        if decoded is None:
            return len(data)
        msgid, _sysid, _compid, payload = decoded
        if msgid == 76:
            command = struct.unpack_from("<H", payload, 28)[0]
            params = struct.unpack_from("<7f", payload, 0)
            self.commands.append((command, params))
            if self.hb_stop_after_start and params[1] > 0:
                self.hb_stop_at = self.clock()
            if self.ack_results:
                result = self.ack_results.pop(0)
                self.pending.append(
                    (self.clock() + self.ack_delay,
                     px4_frame(77, struct.pack("<HB", command, result))))
        return len(data)

    def flush(self):
        return None

    def close(self):
        self.closed += 1


def run_scenario(argv_extra=None, ask=None, **port_kwargs):
    clock = FakeClock()
    port = FakePort(clock, **port_kwargs)
    argv = ["--motor", "1", "--pct", "8", "--secs", "1", "--px4-timeout", "2"]
    argv += list(argv_extra or [])
    args = parse_args(argv)
    args.alive_file = ""            # 离线场景不写监督文件
    app = MotorTest(args, clock=clock, sleeper=clock.advance, ask=ask)
    app.port = port
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        code = app.run()
    app.cleanup()
    return code, captured.getvalue(), app, port


def self_check():
    failures = []

    def expect(name, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'} {name}{'' if ok else ' ' + detail}")
        if not ok:
            failures.append(name)

    # ---- 1. 帧与参数 ----
    frame = command_long_frame(3, 310, motor_test_params(2, 8.0, 2.0), 1, 1)
    decoded = decode_frame(frame)
    expect("command_long frame CRC and header", decoded is not None)
    if decoded:
        msgid, sysid, compid, payload = decoded
        command, target_system, target_component, _conf = struct.unpack_from(
            "<HBBB", payload, 28)
        params = struct.unpack_from("<7f", payload, 0)
        expect("msgid is COMMAND_LONG(76)", msgid == 76, f"got {msgid}")
        expect("source is 42/191", (sysid, compid) == (42, 191))
        expect("command is 310", command == 310, f"got {command}")
        expect("target is 1/1", (target_system, target_component) == (1, 1))
        expect("param1 is normalized 8 percent",
               abs(params[0] - 0.08) < 1e-6, str(params))
        expect("param2 is the timeout", params[1] == 2.0)
        expect("param3 and param4 are zero",
               params[2] == 0.0 and params[3] == 0.0)
        expect("param5 selects motor 2", params[4] == 2.0)

    stop = stop_params(2)
    expect("stop command releases motor 2",
           stop[0] == 0.0 and stop[1] == 0.0 and stop[4] == 2.0)

    ack = build_frame(77, struct.pack("<HB", 310, 3), 5)
    ack_decoded = decode_frame(ack)
    expect("command_ack parses", ack_decoded is not None)
    if ack_decoded:
        command, result = decode_command_ack(ack_decoded[3])
        expect("ack command/result", (command, result) == (310, 3))
        expect("result text", describe_result(3) == "UNSUPPORTED")

    hb = heartbeat_frame(7)
    hb_decoded = decode_frame(hb)
    expect("heartbeat parses", hb_decoded is not None)
    if hb_decoded:
        info = decode_heartbeat(hb_decoded[3])
        expect("heartbeat is companion type 18", info["type"] == 18)

    for bad in (0, 5, -1):
        try:
            motor_test_params(bad, 8.0, 2.0)
            expect(f"motor {bad} rejected", False, "no exception")
        except ValueError:
            expect(f"motor {bad} rejected", True)
    try:
        motor_test_params(1, 100.0, 2.0)
        expect("percent above cap rejected", False, "no exception")
    except ValueError:
        expect("percent above cap rejected", True)

    # ---- 2. 电池报文固定二进制向量（按线路字段顺序） ----
    vector = battery_status_vector([4000, 4000, 4000, 4000])
    decoded = decode_frame(vector)
    expect("battery vector CRC", decoded is not None)
    if decoded:
        info = decode_battery_status(decoded[3])
        expect("battery payload length", info["payload_len"] == 36,
               str(info["payload_len"]))
        expect("battery parse valid", info["valid"] is True)
        expect("cell voltages read at offset 10",
               abs(info["voltages_sum_v"] - 16.0) < 1e-6,
               str(info["voltages_sum_v"]))
        expect("current read at offset 30", abs(info["current_a"] - 1.28) < 1e-6,
               str(info["current_a"]))
        expect("remaining read at offset 35", info["remaining"] == 86,
               str(info["remaining"]))
        expect("id read at offset 32", info["id"] == 0, str(info["id"]))

    # PX4 常见形态：只在 voltages[0] 放总电压，逐电芯计数不可靠
    single = decode_battery_status(
        decode_frame(battery_status_vector([16226]))[3])
    expect("single-value battery reports one cell", single["cells"] == 1,
           str(single["cells"]))
    expect("single-value battery keeps the total voltage",
           abs(single["first_cell_v"] - 16.226) < 1e-6,
           str(single["first_cell_v"]))

    expect("short battery payload is invalid",
           decode_battery_status(b"\x00" * 8)["valid"] is False)

    sys_vector = decode_frame(sys_status_vector(16226))
    expect("sys_status vector CRC", sys_vector is not None)
    if sys_vector:
        info = decode_sys_status(sys_vector[3])
        expect("sys_status payload length", info["payload_len"] == 31,
               str(info["payload_len"]))
        expect("sys_status voltage read at offset 14",
               abs(info["voltage_v"] - 16.226) < 1e-6, str(info["voltage_v"]))
        expect("sys_status current read at offset 16",
               abs(info["current_a"] - 1.28) < 1e-6, str(info["current_a"]))
        expect("sys_status remaining read at offset 30",
               info["remaining"] == 86, str(info["remaining"]))
    expect("short sys_status payload is invalid",
           decode_sys_status(b"\x00" * 10)["valid"] is False)

    # ---- 3a. 状态机：正常路径，但没有动作后确认 ----
    code, text, app, port = run_scenario(ack_results=[0, 0, 0])
    expect("protocol accepted without attestation exits 4", code == 4,
           f"code={code}")
    expect("verdict is not FULL_PASS", "PROTOCOL_ACCEPTED_NOT_ATTESTED" in text)
    labels = [tx["label"] for tx in app.tx_history]
    expect("happy path transactions", labels == ["clear", "start", "stop"],
           str(labels))
    expect("happy path stop frame sent", app.stop_frame_sent is True)
    expect("happy path stop ack unambiguous",
          app.stop_ack == 0 and not app.ack_attribution_unreliable)
    expect("happy path no abandoned transaction", app.abandoned == [])
    expect("happy path battery verified", app.battery_ok is True)
    expect("no physical claim without attestation",
          app.attestations.get("motor_observed") is None)

    # ---- 3b. 动作后确认全为真才允许 FULL_PASS ----
    code, text, app, port = run_scenario(ack_results=[0, 0, 0],
                                         ask=lambda _q: True)
    expect("attested run exits 0", code == 0, f"code={code}")
    expect("attested verdict is FULL_PASS", "FULL_PASS" in text)
    expect("attestation timestamped", app.attested_at is not None)

    code, text, app, port = run_scenario(ack_results=[0, 0, 0],
                                         ask=lambda _q: False)
    expect("negative attestation is not a pass", code == 4, f"code={code}")

    # ---- 3c. 无4S探测模式：不要求先验语义确认，不产生物理结论 ----
    code, text, app, port = run_scenario(["--no-4s-probe"], ack_results=[0, 0, 0],
                                         emit_sys_status=False,
                                         ask=lambda _q: True)
    expect("probe mode exits 5", code == 5, f"code={code}")
    expect("probe verdict has no physical claim",
          "PROBE_COMPLETED_NO_PHYSICAL_CLAIM" in text)
    expect("probe skips the battery gate", app.require_battery is False)
    expect("probe asks about the published actuator_test command",
          app.attestations.get("actuator_test_entered") is True)
    expect("probe does not claim an unobservable topic clear",
          app.attestations.get("actuator_test_cleared") is None)
    expect("probe records no physical observation",
          app.attestations.get("motor_observed") is None)
    expect("probe mode recorded in report",
          app.build_report()["mode"] == "probe")
    # 探测模式必须先等 param2 到期取证，再发清理停止
    expect("probe waits for the timeout window",
          app.px4_timeout_elapsed is True)
    stop_tx = [tx for tx in app.tx_history if tx["label"] == "stop"]
    expect("probe sends exactly one cleanup stop", len(stop_tx) == 1,
           str([tx["label"] for tx in app.tx_history]))
    if stop_tx:
        expect("cleanup stop is sent after param2 expired",
              stop_tx[0]["sent_at"] >= app.px4_deadline,
              f"stop_at={stop_tx[0]['sent_at']:.2f} "
              f"deadline={app.px4_deadline:.2f}")
    expect("no stop frame before the timeout window",
          app.build_report()["transactions"][1]["label"] == "start"
          and app.build_report()["transactions"][1]["params"][0] > 0.0)

    # ---- 3d. 实物模式必须走电池门禁 ----
    gates = parse_args(["--motor", "1", "--pct", "8", "--secs", "1",
                        "--battery", "skip", "--firmware-semantics-verified",
                        "--preflight-ok"])
    expect("live mode rejects --battery skip",
          validate_mode(gates) is not None, str(validate_mode(gates)))
    probe_gates = parse_args(["--motor", "1", "--pct", "8", "--secs", "1",
                              "--no-4s-probe"])
    expect("probe mode accepts the gate check", validate_mode(probe_gates) is None)
    probe_bad = parse_args(["--motor", "1", "--pct", "8", "--secs", "1",
                            "--no-4s-probe", "--firmware-semantics-verified"])
    expect("probe rejects prior semantics claim",
          validate_mode(probe_bad) is not None)
    live_ok = parse_args(["--motor", "1", "--pct", "8", "--secs", "1",
                          "--firmware-semantics-verified", "--preflight-ok"])
    expect("live mode with both attestations passes the gate",
          validate_mode(live_ok) is None, str(validate_mode(live_ok)))

    # ---- 4. 启动 ACK 等待期间发生故障：必须真正发出停止命令 ----
    # 启动命令不回 ACK（事务悬挂），随后心跳中断触发故障。
    late_ack = px4_frame(77, struct.pack("<HB", 310, 0))
    code, text, app, port = run_scenario(
        ["--heartbeat-timeout", "1", "--ack-timeout", "2"],
        hb_period=0.5, hb_stop_after_start=True, ack_results=[0],
        inject=[(4.0, late_ack)])
    expect("fault while start ack is pending exits 1", code == 1, f"code={code}")
    expect("start transaction abandoned",
          bool(app.abandoned) and app.abandoned[-1]["label"] == "start",
          str(app.abandoned))
    expect("stop frame really sent after the fault",
          app.stop_frame_sent is True)
    stop_frames = [params for command, params in port.commands
                   if command == 310 and params[1] == 0.0]
    expect("a release-control 310 frame was written", bool(stop_frames),
           str(port.commands))
    expect("late same-id ack marked unreliable",
          app.ack_attribution_unreliable is True)
    expect("stop ack received after abandon is not a confirmation",
          app.stop_ack == 0
          and app.build_report()["stop_ack_unambiguous"] is False,
          f"stop_ack={app.stop_ack}")
    expect("abandon logged", "ABANDON" in text)

    # ---- 5. 迟到的 clear ACK 进入 start 窗口 ----
    late = px4_frame(77, struct.pack("<HB", 310, 0))
    code, text, app, port = run_scenario(
        ["--ack-timeout", "1"],
        ack_results=[0, 0, 0], inject=[(1.2, late)], hb_period=0.5)
    expect("late clear ack aborts before start", code == 1, f"code={code}")
    expect("late ack flagged ambiguous", app.ambiguous_ack is True)
    expect("no start command after ambiguous ack",
          len(port.commands) == 1, str(port.commands))
    expect("no motor start frame written",
          all(params[1] == 0.0 for _c, params in port.commands))

    # ---- 6. 缺少电池报文时不得发出命令 ----
    code, text, app, port = run_scenario(
        ["--startup-timeout", "5"], emit_sys_status=False,
        ack_results=[0, 0, 0])
    expect("no sys_status blocks the command",
          code == 1 and len(port.commands) == 0,
          f"code={code} commands={len(port.commands)}")
    expect("battery gate logged", "SYS_STATUS not received" in text)

    print(f"\nself-check: {'OK' if not failures else 'FAILED: ' + str(failures)}")
    return 0 if not failures else 1


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Nano single-motor VEHICLE_CMD_ACTUATOR_TEST(310) command",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "exit: 0 full pass (live, attested after the action), 1 fault, "
            "2 usage/environment, 3 aborted, 4 protocol accepted but not "
            "attested, 5 no-4S probe completed with no physical claim\n"
            "--no-4s-probe is the only mode that does not require "
            "--firmware-semantics-verified; the probe is what establishes it.\n"
            "this file is unapproved: do not upload or run live until the "
            "staged review allows it; --dry-run and --self-check are offline."
        ),
    )
    parser.add_argument("--device", default="/dev/ttyS1")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--target-system", type=int, default=1)
    parser.add_argument("--target-component", type=int, default=1)
    parser.add_argument("--motor", type=int, required=True,
                        help="single motor 1..4; 0 (all motors) is never allowed")
    parser.add_argument("--pct", type=float, required=True,
                        help=f"throttle percent, >0 and <= {MAX_OUTPUT_PERCENT}")
    parser.add_argument("--secs", type=float, required=True,
                        help=f"observe seconds, >0 and <= {MAX_TEST_SECONDS}")
    parser.add_argument("--px4-timeout", type=float, default=None,
                        help="param2 timeout in seconds; must exceed --secs and "
                             "must be confirmed on this firmware first")

    parser.add_argument("--startup-timeout", type=float, default=10.0)
    parser.add_argument("--heartbeat-timeout", type=float, default=3.0)
    parser.add_argument("--ack-timeout", type=float, default=3.0)
    parser.add_argument("--ack-settle", type=float, default=0.5)
    parser.add_argument("--verify-margin", type=float, default=0.5)
    parser.add_argument("--stop-tail-seconds", type=float, default=3.0)
    parser.add_argument("--max-process-seconds", type=float, default=60.0)

    parser.add_argument("--battery", choices=["require", "skip"], default="require")
    parser.add_argument("--expect-cells", type=int, default=4)
    parser.add_argument("--min-cell-voltage", type=float, default=3.2)
    parser.add_argument("--max-cell-voltage", type=float, default=4.25)

    parser.add_argument("--firmware-semantics-verified", action="store_true",
                        help="operator attests param2 timeout semantics were "
                             "confirmed on this firmware; not required by "
                             "--no-4s-probe, which is what establishes it")
    parser.add_argument("--preflight-ok", action="store_true",
                        help="operator attests the physical gates; not "
                             "required by --no-4s-probe (no motor power)")
    parser.add_argument("--no-4s-probe", action="store_true",
                        help="no-4S protocol probe: ESC unpowered, no motion "
                             "possible; used to establish the ACK and param2 "
                             "semantics. Operator asserts 4S is disconnected")

    parser.add_argument("--alive-file", default=ALIVE_PATH)
    parser.add_argument("--report-file", default=None)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--self-check", action="store_true",
                        help="offline frame/decode/guard check, no serial")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.motor not in ALLOWED_MOTORS:
        parser.error(f"--motor must be one of {ALLOWED_MOTORS}; 0 would mean "
                     "all motors and is never allowed")
    if not (0 < args.pct <= MAX_OUTPUT_PERCENT):
        parser.error(f"--pct must be >0 and <= {MAX_OUTPUT_PERCENT}; "
                     "out-of-range values are rejected, not clamped")
    if not (0 < args.secs <= MAX_TEST_SECONDS):
        parser.error(f"--secs must be >0 and <= {MAX_TEST_SECONDS}")
    if args.px4_timeout is None:
        args.px4_timeout = args.secs + 1.0
    if args.px4_timeout <= args.secs:
        parser.error("--px4-timeout must be greater than --secs")
    if args.px4_timeout > MAX_PX4_TIMEOUT_SECONDS:
        parser.error(f"--px4-timeout must be <= {MAX_PX4_TIMEOUT_SECONDS}")
    # 静默窗口必须覆盖完整的 ACK 超时窗口，否则迟到的 clear ACK 会污染 start 事务
    if args.ack_settle < args.ack_timeout:
        args.ack_settle = args.ack_timeout
    for name in ("startup_timeout", "heartbeat_timeout", "ack_timeout",
                 "ack_settle", "verify_margin", "stop_tail_seconds",
                 "max_process_seconds"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be greater than zero")
    return args


def validate_mode(args):
    """模式门禁。返回错误文本或 None。"""
    if args.no_4s_probe:
        if args.firmware_semantics_verified:
            return ("--firmware-semantics-verified must not be used with "
                    "--no-4s-probe: the probe is what establishes it")
        return None
    if args.battery == "skip":
        return ("--battery skip is only allowed for --no-4s-probe; live mode "
                "requires the pack-voltage gate")
    if not args.firmware_semantics_verified:
        return ("live mode requires --firmware-semantics-verified: the param2 "
                "timeout semantics must be established by a --no-4s-probe run "
                "first")
    if not args.preflight_ok:
        return ("live mode requires --preflight-ok (props removed, single "
                "motor fixed, people clear of the rotation plane, 4S can be "
                "pulled immediately, QGC Actuator Testing not in use)")
    return None


def dry_run(args):
    """离线：不导入 pyserial、不打开串口、不加锁、不发送任何报文。"""
    print("DRY RUN OK (no serial device, no lock, no MAVLink traffic)")
    print(f"device={args.device} baud={args.baud} "
          f"target={args.target_system}/{args.target_component}")
    print("backend=310 (VEHICLE_CMD_ACTUATOR_TEST; PX4 1.16.2 native path)")
    print(f"motor={args.motor} pct={args.pct} observe={args.secs}s "
          f"px4_timeout={args.px4_timeout}s")
    print(f"hard caps: pct<={MAX_OUTPUT_PERCENT} secs<={MAX_TEST_SECONDS} "
          f"motors={ALLOWED_MOTORS}")
    print("transaction plan (param1..param7):")
    print("  clear ", list(stop_params(args.motor)))
    print("  start ", list(motor_test_params(args.motor, args.pct,
                                             args.px4_timeout)))
    print("  stop  ", list(stop_params(args.motor)))
    order = ("OBSERVE->VERIFY->STOP" if args.no_4s_probe
             else "OBSERVE->STOP->VERIFY")
    print(f"states: CHECK->CLEAR->QUIET->SEND->ACK->{order}->COMPLETE")
    if args.no_4s_probe:
        print("probe order: wait for param2 + margin BEFORE sending the "
              "cleanup RELEASE_CONTROL command; actuator_test is a command "
              "topic and is not republished when the mixer timeout expires")
    print("runtime: standard library + pyserial only; pymavlink/mavutil is "
          "never imported")
    if args.no_4s_probe:
        print("mode=no-4s-probe: does NOT require --firmware-semantics-verified "
              "or --preflight-ok; ESC unpowered, no motion possible")
        print("evidence to capture: COMMAND_ACK(310), actuator_test action=1 "
              "function=101 value=0.08 timeout_ms=2000 (QGC NSH: "
              "listener actuator_test 1), full param2 timeout wait, and the "
              "cleanup RELEASE_CONTROL ACK")
        if args.firmware_semantics_verified:
            print("live mode would refuse: --firmware-semantics-verified must "
                  "not be combined with --no-4s-probe")
    else:
        missing = []
        if not args.firmware_semantics_verified:
            missing.append("--firmware-semantics-verified")
        if not args.preflight_ok:
            missing.append("--preflight-ok")
        if missing:
            print(f"live mode would refuse to run without: {', '.join(missing)}")
    print("physical conclusions are only taken after the action, by prompted "
          "attestation; pre-action confirm switches do not exist")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    if "--self-check" in argv:
        # 离线自检不需要 --motor/--pct/--secs，因此先于参数校验处理
        return self_check()
    args = parse_args(argv)
    if args.dry_run:
        return dry_run(args)

    if args.no_4s_probe:
        # 探测模式本身就是确认 param2 语义的手段，不能要求先验的语义确认。
        print("MODE no-4s-probe: 4S must be disconnected; the ESC is unpowered "
              "and no motor can move. Only the ACK and the PX4 internal "
              "actuator_test state are being established.", file=sys.stderr)
    error = validate_mode(args)
    if error:
        print(f"ERROR {error}", file=sys.stderr)
        return EXIT_USAGE
    if fcntl is None:
        print("ERROR live mode requires Linux flock", file=sys.stderr)
        return EXIT_USAGE

    try:
        import serial as loaded_serial
    except ModuleNotFoundError:
        print("ERROR live mode requires pyserial", file=sys.stderr)
        return EXIT_USAGE

    app = MotorTest(args, ask=prompt_yes_no)

    def request_stop(_signum, _frame):
        if app.abort_reason is None:
            app.abort_reason = "operator interrupt"
            app.log("stop requested by signal; no serial write from the handler")
        app.running = False

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        app.acquire_lock()
    except RuntimeError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        app.open_port(loaded_serial)
    except Exception as exc:
        print(f"ERROR opening {args.device}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return EXIT_USAGE

    try:
        return app.run()
    except Exception as exc:
        # 异常也要先走统一的停止尾流程，再生成报告
        app.log(f"ERROR {type(exc).__name__}: {exc}")
        app.fault(f"exception: {type(exc).__name__}: {exc}")
        app.run_stop_tail()
        app.finalize()
        return app.exit_code
    finally:
        app.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
