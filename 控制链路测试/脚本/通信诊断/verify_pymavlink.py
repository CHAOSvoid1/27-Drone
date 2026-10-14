#!/usr/bin/env python3
"""Import-only pymavlink verification for the LicheeRV Nano."""

import os
import sys
import traceback

os.environ.setdefault("MAVLINK_DIALECT", "common")


def status(text):
    print(text, flush=True)


def main():
    try:
        status("verify: start")
        status(f"python: {sys.version}")

        status("verify: importing future")
        import future
        status(f"future: {getattr(future, '__version__', 'unknown')}")

        status("verify: importing lxml")
        import lxml
        status(f"lxml: {getattr(lxml, '__version__', 'unknown')}")

        status("verify: importing pymavlink package")
        import pymavlink
        status(f"pymavlink: {getattr(pymavlink, '__version__', 'unknown')}")

        status("verify: importing generated common MAVLink v1 dialect")
        from pymavlink.dialects.v10 import common as common_v10
        status(f"common PX4 enum: {common_v10.MAV_AUTOPILOT_PX4}")

        status("mavutil: intentionally not imported on the 128 MiB Nano")
        status("runtime API: pymavlink.dialects.v10.common + pyserial")
        status("PYMAVLINK VERIFY OK")
        return 0
    except BaseException:
        status("PYMAVLINK VERIFY FAILED")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
