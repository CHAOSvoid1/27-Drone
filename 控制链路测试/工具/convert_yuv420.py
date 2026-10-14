from pathlib import Path
import sys

import numpy as np
from PIL import Image


def convert(source: Path, destination: Path, width: int, height: int, layout: str) -> None:
    raw = np.fromfile(source, dtype=np.uint8)
    expected = width * height * 3 // 2
    if raw.size != expected:
        raise ValueError(f"文件为 {raw.size} 字节，预期 {expected} 字节")

    y_size = width * height
    y = raw[:y_size].reshape(height, width).astype(np.float32)
    chroma = raw[y_size:].reshape(height // 2, width)
    if layout == "nv21":
        v = chroma[:, 0::2]
        u = chroma[:, 1::2]
    elif layout == "nv12":
        u = chroma[:, 0::2]
        v = chroma[:, 1::2]
    else:
        raise ValueError("layout 必须是 nv21 或 nv12")

    u = np.repeat(np.repeat(u, 2, axis=0), 2, axis=1).astype(np.float32) - 128.0
    v = np.repeat(np.repeat(v, 2, axis=0), 2, axis=1).astype(np.float32) - 128.0
    c = y - 16.0
    r = 1.164 * c + 1.596 * v
    g = 1.164 * c - 0.392 * u - 0.813 * v
    b = 1.164 * c + 2.017 * u
    rgb = np.clip(np.dstack((r, g, b)), 0, 255).astype(np.uint8)
    Image.fromarray(rgb, "RGB").save(destination)


if __name__ == "__main__":
    if len(sys.argv) != 6:
        raise SystemExit("用法: convert_yuv420.py 输入.yuv 输出.png 宽 高 nv21|nv12")
    convert(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5].lower())
