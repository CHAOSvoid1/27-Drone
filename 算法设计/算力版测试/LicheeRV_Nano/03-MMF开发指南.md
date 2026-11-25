# CVI MMF 开发指南（SG2002 / LicheeRV Nano）

> **MMF = Media Processing Framework**，CVITEK（Sophgo）多媒体 SDK 的框架层，对上提供 MPI 接口，对下驱动 VI / MIPI / ISP / VPSS / VENC / VO 等硬件模块。
>
> 本文面向"要把摄像头接进自己程序"的开发者，说明**用什么、怎么编、怎么写、怎么调**。
>
> 配套文档：[01-构建镜像.md](01-构建镜像.md)（编译烧录）、[02-GC4653摄像头测试.md](02-GC4653摄像头测试.md)（硬件验证与出图）。

---

## 1. 先理解架构：为什么它和普通 V4L2 不一样

### 1.1 分层

```
┌──────────────────────────────────────────────┐
│  应用层   my_app / sample_vio / sample_venc   │
├──────────────────────────────────────────────┤
│  MPI 接口  libcvi_*（cvi_sys.h / cvi_vi.h…）  │
├──────────────────────────────────────────────┤
│  中间件    middleware/v2/modules、component   │
├──────────────────────────────────────────────┤
│  内核驱动  soph_vi / soph_vpss / soph_isp /   │
│           soph_mipi_rx / soph_vb / soph_base  │
├──────────────────────────────────────────────┤
│  通信桥    cvi-rtos-cmdqu（Linux ↔ RTOS）     │
├──────────────────────────────────────────────┤
│  RTOS 小核 VI / ISP 实时采集、AE/AWB 收敛     │
├──────────────────────────────────────────────┤
│  硬件      MIPI CSI RX → VI → ISP → VPSS …    │
└──────────────────────────────────────────────┘
```

### 1.2 三个必须记住的事实

| 事实 | 影响 |
|------|------|
| **SG2002 是大小核**，VI/ISP 实时处理跑在 RTOS 上 | Linux 的 `i2cdetect` 看不到 sensor；不能用普通 V4L2 思路 |
| **VB/VI/ISP 是全局独占资源** | 同一时刻只能一个进程占用；第二个进程会失败 |
| **崩溃不会自动释放资源** | 跳过 `CVI_VB_Exit` 会残留，必须重启板子 |

> 这三点是排查所有"诡异失败"的出发点。

### 1.3 模块速查

| 模块 | 头文件 | 职责 |
|------|--------|------|
| SYS | `cvi_sys.h` | 全局初始化、模块绑定 |
| VB | `cvi_vb.h` | 视频缓冲池（**最重要，最容易出错**） |
| VI | `cvi_vi.h` | 视频输入（采集） |
| MIPI | `cvi_mipi.h` | MIPI CSI 接收端配置 |
| ISP | `cvi_isp.h` | 图像信号处理、3A（AE/AWB） |
| VPSS | `cvi_vpss.h` | 缩放、裁剪、格式转换 |
| VENC | `cvi_venc.h` | H.264/H.265/JPEG 编码 |
| VDEC | `cvi_vdec.h` | 解码 |
| VO | `cvi_vo.h` | 显示输出 |
| RGN | `cvi_region.h` | OSD 叠加 |
| DWA | `cvi_dwa.h` | 畸变矫正 |
| IVE | `cvi_ive.h` | 硬件加速图像算子 |
| TPU | `cvi_tpu.h` | NPU 推理 |

---

## 2. 源码目录速查

| 路径 | 内容 |
|------|------|
| `middleware/v2/include/` | **所有 MPI 头文件** |
| `middleware/v2/lib/` | 编译产物库（`libsns_*`、MPI 库） |
| `middleware/v2/sample/` | 官方示例（**最佳学习材料**） |
| `middleware/v2/sample/common/` | `sample_common_*` 封装层 |
| `middleware/v2/modules/` | 各模块 MPI 实现（`vpu`、`venc`、`isp` …） |
| `middleware/v2/component/isp/sensor/<arch>/` | **各 sensor 驱动** |
| `middleware/v2/Makefile.param` | 编译参数模板（`MW_INC`、`MW_LIB`…） |
| `osdrv/interdrv/v2/` | 内核驱动（`vb.c`、`vi`、`isp`…） |

---

## 3. 编译环境

### 3.1 工具链

```
host-tools/gcc/riscv64-linux-musl-x86_64/bin/riscv64-unknown-linux-musl-gcc
host-tools/gcc/riscv64-linux-musl-x86_64/bin/riscv64-unknown-linux-musl-g++
```

验证：

```bash
cd ~/Desktop/study/RMDrone/sources/LicheeRV-Nano-Build
host-tools/gcc/riscv64-linux-musl-x86_64/bin/riscv64-unknown-linux-musl-gcc -v
```

### 3.2 加载构建环境

```bash
source build/cvisetup.sh
defconfig sg2002_licheervnano_sd
```

这一步会导出 `CROSS_COMPILE`、`MW_INC`、`MW_LIB`、`KERNEL_INC` 等变量，并设置 `TOP_DIR`。

> `build/cvisetup.sh` 里设置的是 **shell 函数与变量**，每开新终端都要重新 `source`。

### 3.3 Makefile.param 关键变量

| 变量 | 含义 |
|------|------|
| `CC` / `CXX` / `LD` / `AR` | 交叉编译器 |
| `MW_INC` | 中间件头文件路径 |
| `ISP_INC` | ISP 头文件路径 |
| `KERNEL_INC` | 内核 UAPI 头文件路径（**需先编过内核**） |
| `MW_LIB` | 中间件库输出目录 |
| `CFLAGS` / `LDFLAGS` | 通用编译/链接参数 |

> `KERNEL_INC` 指向 `<内核输出>/riscv/usr/include`。若未编译内核，编 app 会报 `linux/cvi_type.h: No such file`。

### 3.4 三种编译方式

| 方式 | 命令 | 适用 |
|------|------|------|
| 整包 | `build_all` | 出镜像 |
| 单编 middleware | `build_middleware` | 改了 sensor/ISP 相关 |
| 单编 sample | `make -C middleware/v2/sample/sensor_test` | 改自己的 app |

### 3.5 自己的程序：Makefile 模板

推荐**做法：复制一个 sample 目录再改**，这样链接参数不用猜。

```makefile
# ---------------------------------------------------------------
# RMDrone 采集程序 Makefile
# 用法：在已 source build/cvisetup.sh 的 shell 里执行 make
# ---------------------------------------------------------------
PARAM_FILE ?= ../../../Makefile.param
include $(PARAM_FILE)

TARGET   := rmdrone_vi
SRCS     := main.c
OBJS     := $(SRCS:.c=.o)
DEPS     := $(SRCS:.c=.d)

# 头文件
CFLAGS   += -I$(MW_INC) -I$(ISP_INC)
# 需要内核头（用了 dma-buf 等）时再加：
# CFLAGS += -I$(KERNEL_INC)

# 链接库（按实际需要的模块裁剪）
LDFLAGS  += -L$(MW_LIB)
LDLIBS   += -lsys -lvb -lvi -lvpss -lisp -lvenc -lsnsr_i2c -lpthread -lm

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS) $(LDFLAGS) $(LDLIBS)

%.o: %.c
	$(CC) $(CFLAGS) $(DEPFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) $(DEPS) $(TARGET)

-include $(DEPS)
```

> **不确定要链哪些库**时，直接看 `middleware/v2/sample/sample_vio/Makefile` 的 `LDLIBS`，按需删减。

### 3.6 部署到板子

```bash
# 编译产物拷到板子
scp rmdrone_vi root@licheervnano-8050.local:/root/

# 或用 sshfs 挂载后直接复制
cp rmdrone_vi ~/licheerv/root/
```

板子上运行前确认库路径：

```bash
export LD_LIBRARY_PATH=/mnt/system/usr/lib:$LD_LIBRARY_PATH
ldd /root/rmdrone_vi        # 不应出现 not found
```

---

## 4. 程序骨架

### 4.1 初始化顺序（不能乱）

```
VB_SetConfig → VB_Init
      ↓
   SYS_Init
      ↓
解析 sensor_cfg.ini → sensor 注册（stSnsXxx_Obj）
      ↓
  MIPI 配置（mclk / reset / lane）
      ↓
   VI Dev → VI Chn
      ↓
  ISP_Init → ISP_Run（起 ISP 线程）
      ↓
   VPSS/VENC/VO 初始化
      ↓
     SYS_Bind
```

### 4.2 反初始化顺序（逆序）

```
SYS_UnBind
  → VI_DisableChn → VI_DisableDev
  → MIPI_DisableMipiClock → MIPI_DisableSensorClock
  → ISP_Exit
  → VB_Exit
  → SYS_Exit
```

> ⚠️ **务必用 `goto` / 统一的 cleanup 保证异常路径也能走完释放**，否则 VB 池残留，下一次运行必失败。

### 4.3 骨架代码

```c
#include <stdio.h>
#include <string.h>
#include <pthread.h>

#include "cvi_sys.h"
#include "cvi_vb.h"
#include "cvi_vi.h"
#include "cvi_vpss.h"
#include "cvi_isp.h"
#include "cvi_mipi.h"
#include "cvi_sns_ctrl.h"

#define PIC_W      2560
#define PIC_H      1440
#define VB_BLK_SZ  (PIC_W * PIC_H * 3 / 2)   /* NV12: w*h*1.5 */
#define VB_BLK_CNT 6

static CVI_S32 sys_vb_init(void)
{
    VB_CONFIG_S cfg;
    memset(&cfg, 0, sizeof(cfg));
    cfg.u32MaxPoolCnt             = 1;
    cfg.astCommPool[0].u32BlkSize = VB_BLK_SZ;
    cfg.astCommPool[0].u32BlkCnt  = VB_BLK_CNT;

    CVI_S32 ret = CVI_VB_SetConfig(&cfg);
    if (ret != CVI_SUCCESS) { printf("VB_SetConfig fail 0x%x\n", ret); return ret; }

    ret = CVI_VB_Init();
    if (ret != CVI_SUCCESS) { printf("VB_Init fail 0x%x\n", ret); return ret; }

    return CVI_SYS_Init();
}

static CVI_S32 vi_start(void)
{
    /* 1) 解析 /mnt/data/sensor_cfg.ini，得到 sensor 名称、i2c、lane */
    SAMPLE_SNS_TYPE_E snsType;
    if (SAMPLE_COMM_SNS_ParseIni("/mnt/data/sensor_cfg.ini", &snsType) != 0)
        return -1;

    /* 2) 探测并注册 sensor（内部调用 gc4653_probe / gc4653_init） */
    if (SAMPLE_COMM_VI_StartSensor(snsType) != 0) return -1;

    /* 3) MIPI：时钟 / 复位 / lane 属性 */
    if (SAMPLE_COMM_VI_StartMIPI(0) != 0) return -1;

    /* 4) VI 设备 */
    if (SAMPLE_COMM_VI_StartDev(0, snsType) != 0) return -1;

    /* 5) VI 通道（此处会向 VB 池申请块，池不足直接失败） */
    if (SAMPLE_COMM_VI_StartChn(0, &(SAMPLE_VI_CHN_S){ .enChn=0,
            .stSize={PIC_W, PIC_H}, .enPixFormat=PIXEL_FORMAT_RGB_BAYER_10B }) != 0)
        return -1;

    /* 6) ISP 初始化 + 线程 */
    if (SAMPLE_COMM_VI_StartIsp(0) != 0) return -1;

    return 0;
}

int main(void)
{
    if (sys_vb_init() != CVI_SUCCESS) goto EXIT_VB;
    if (vi_start()    != 0)           goto EXIT_SYS;

    /* ---- 业务主循环 ---- */
    while (1) {
        VIDEO_FRAME_INFO_S frame;
        /* 方式 A：绑定到 VPSS 后用 VPSS 取帧
         * 方式 B：直接从 VI 取帧 */
        if (CVI_VI_GetChnFrame(0, 0, &frame, 2000) == CVI_SUCCESS) {
            /* TODO: frame 中是图像数据，可送算法 / 编码 / 存盘 */
            CVI_VI_ReleaseChnFrame(0, 0, &frame);
        }
    }

EXIT_APP:
    SAMPLE_COMM_VI_StopVi(0);      /* 内部含 UnBind / DisableChn / DisableDev */
    SAMPLE_COMM_ISP_Stop(0);
EXIT_SYS:
    CVI_SYS_Exit();
EXIT_VB:
    CVI_VB_Exit();
    return 0;
}
```

> 上面用了 `SAMPLE_COMM_*` 封装（来自 `sample/common/`），**上手阶段强烈建议先用封装**，跑通后再按需替换成裸 MPI 调用。
>
> 若只把 sample 当作参考，把 `sample/common/` 整个目录一起编进来可省掉大量样板代码。

---

## 5. VB 池：最容易出错的一环

### 5.1 块大小怎么算

| 格式 | 每像素字节 | 1440p 一帧 |
|------|-----------|-----------|
| **RAW10**（sensor 原始） | 1.25 | 4,608,000 |
| **RAW12**（sensor 原始） | 1.5 | **5,529,600** ← GC4653 实测 |
| NV12 / YUV420SP | 1.5 | 5,529,600 |
| YUYV422 | 2.0 | 7,372,800 |
| RGB888 | 3.0 | 11,059,200 |

> 经验：**块大小 = 宽 × 高 × 每像素字节，向上取整到 4K/页对齐**。
>
> `sample_vio` 的 1080p 配置用 3,110,400；`sensor_test` 的 1440p 配置用 5,652,480。后者比理论值大一点，是加了边距。

### 5.2 块数量估算

```
块数 ≥ 同时在途的帧数
     = 采集队列深度 + 处理队列深度 + 显示/编码队列深度
```

工程上常用：

| 场景 | 建议块数 |
|------|---------|
| 单路采集 + 处理 | 4–6 |
| 采集 + 编码 + 显示 | 6–8 |
| 多路 | 每路单独算 |

### 5.3 内存从哪来

VB 池的物理内存来自 **dts 里的 `ion` 预留区**（LicheeRV Nano 实测 **105 MB**）。

```
占用量 = 块大小 × 块数
```

例：`5,529,600 × 6 ≈ 33 MB`，105 MB 内很宽裕。

### 5.4 三个致命细节

| 细节 | 说明 |
|------|------|
| **只初始化一次** | `CVI_VB_Init` 之后 `SetConfig` 无效，日志：`vb has already inited, set_config cmd has no effect` |
| **块太小会失败** | 日志：`vb_get_block_with_id: No valid pool for size(N)` |
| **崩溃不释放** | 必须重启板子才能恢复干净状态 |

### 5.5 运行时查看 VB 状态

```bash
cat /proc/cvitek/vb
```

输出含 `COMMON POOL CONFIG`（PoolId / Size / Count）与每个块的占用者（VI/VPSS/VENC…）。

---

## 6. 各模块使用要点

### 6.1 SNS（传感器注册）

- 由 `stSns<Name>_Obj` 结构体描述，声明在 `middleware/v2/include/cvi_sns_ctrl.h`
- 必须实现：`probe` / `init` / `exit` / `standby` / `restart` / `write_register` / `read_register`
- `probe` 里通常读芯片 ID 确认在线（GC4653 读 `0x03f0/0x03f1`，期望 `0x4653`）

### 6.2 MIPI

| API | 作用 |
|-----|------|
| `CVI_MIPI_SetSensorClock` | 设置 sensor MCLK 频率 |
| `CVI_MIPI_EnableSensorClock` | 输出 MCLK |
| `CVI_MIPI_ResetSensor` | sensor 复位脚 |
| `CVI_MIPI_SetMipiAttr` | **lane 数 / lane 映射**（用 sensor 的 `xxx_rx_attr`） |
| `CVI_MIPI_EnableMipiClock` / `CVI_MIPI_ResetMipi` | MIPI PHY 时钟与复位 |

> lane 配错 → 有 probe 成功但无图，或 MIPI 超时。

### 6.3 VI

| API | 说明 |
|-----|------|
| `CVI_VI_SetDevAttr` / `EnableDev` | 设备级（接口类型、工作模式） |
| `CVI_VI_SetChnAttr` / `EnableChn` | 通道级（分辨率、像素格式、帧率） |
| `CVI_VI_GetChnFrame` / `ReleaseChnFrame` | 取帧 / 归还 |

> **`EnableChn` 是 VB 池不足的报错点。**

### 6.4 ISP 与 3A

- `CVI_ISP_Init` → `CVI_ISP_Run`（启动内部线程）
- tuning 参数来自 `/mnt/cfg/param/*.bin`，加载成功会打印 `cvi_bin_isp message` / `PQBIN message`
- 运行时打印的 `R:xxxx B:xxxx CT:xxxx` 是 AE/AWB 收敛状态，**数值持续变化**才说明在真实采集

### 6.5 VPSS

| API | 说明 |
|-----|------|
| `CVI_VPSS_CreateGrp` | 创建组 |
| `CVI_VPSS_SetChnAttr` / `EnableChn` | 通道属性（缩放目标尺寸、格式） |
| `CVI_VPSS_StartGrp` | 启动 |
| `CVI_VPSS_GetChnFrame` / `ReleaseChnFrame` | 取帧 |

> **缩放、格式转换（RAW→NV12）都在 VPSS 完成**，不要指望 VI 输出 RGB。

### 6.6 VENC

```c
CVI_VENC_CreateChn(chn, &attr);         /* H264/H265/JPEG */
CVI_VENC_StartRecvFrame(chn, &param);
CVI_VENC_GetStream(chn, &stream, timeout);   /* 拿码流 */
CVI_VENC_ReleaseStream(chn, &stream);
```

### 6.7 SYS 绑定

```c
MMF_CHN_S src = { .enModId = CVI_ID_VI,   .s32DevId = 0, .s32ChnId = 0 };
MMF_CHN_S dst = { .enModId = CVI_ID_VPSS, .s32DevId = 0, .s32ChnId = 0 };
CVI_SYS_Bind(&src, &dst);
```

> 绑定后数据在**硬件/内核内部流转，不经过用户态拷贝**，效率高。
>
> 需要自己处理数据（送算法）时，用 `GetChnFrame` 方式而非绑定。

---

## 7. 典型拓扑

### 7.1 预览（VI → VPSS → VO）

```
VI(0,0) → VPSS(grp0,chn0) → VO(0,0)
```
用 `sample_vio 0` 可体验。**需要接屏幕**，否则 VO 初始化失败。

### 7.2 录像 / 推流（VI → VPSS → VENC）

```
VI(0,0) → VPSS(grp0,chn0) → VENC(chn0) → 文件 / RTSP
```
用 `sample_venc` 可体验，支持 `--viWidth=2560 --viHeight=1440`。

### 7.3 算法处理（VI → 用户态）

```
VI(0,0) --GetChnFrame--> memcpy / mmap --> 你的算法（OpenCV / TPU）
```
适合无人机的视觉处理节点。注意**帧率与拷贝开销**。

### 7.4 零拷贝（推荐给算法）

绑定到 VPSS 后，用 `GetChnFrame` 拿到的是 **DMA-BUF 物理地址**，配合 `CVI_SYS_Mmap` 映射到用户态，避免 `memcpy`。

---

## 8. 常见错误与排查

| 报错 / 现象 | 根因 | 处理 |
|------------|------|------|
| `vb has already inited, set_config cmd has no effect` | VB 池已固化 | reboot 后作为第一个程序运行 |
| `No valid pool for size(N)` | VB 块太小 | 增大 `u32BlkSize`（≥ 一帧大小） |
| `Segmentation fault` 紧跟 OOM | 上一条的后果 | 同上；并检查返回值 |
| `vi_sdk_enable_chn ioctl failed` | 通道使能失败（多为 VB） | 查 `dmesg` 找根本原因 |
| `linux/cvi_type.h: No such file` | 内核未编译，`KERNEL_INC` 无内容 | 先 `build_kernel` |
| `error while loading shared libraries` | 库路径不对/库没进镜像 | 设 `LD_LIBRARY_PATH=/mnt/system/usr/lib` |
| probe 失败 / 读 ID 不符 | I2C 地址错 / 接线不良 / 供电不足 | 核对 `sns_i2c_addr`、排线 |
| probe 成功但无图 | MIPI lane 配置错 | 核对 `xxx_rx_attr.lane_id` |
| 有图但偏色 | tuning bin 不匹配 / 格式错 | 换 `pix_fmt`，核对 tuning |
| 第二个进程起不来 | VI/ISP 被占用 | 杀掉旧进程；必要时 reboot |
| `Permission denied` 打开 `/dev/cvi-*` | 非 root | 用 root 运行 |

---

## 9. 调试手段

| 手段 | 命令 / 位置 |
|------|-----------|
| VB 池状态 | `cat /proc/cvitek/vb` |
| VI 状态 | `cat /proc/cvitek/vi` |
| ISP 状态 | `cat /proc/cvitek/isp` |
| 内核日志 | `dmesg -c > /dev/null` 后复现，再 `dmesg` |
| 保留 sample 日志 | 官方 sample 打印信息很全，优先跑 sample 对照 |
| dump 单帧 | `sensor_test` 菜单 `2 0 1` 导 YUV |
| 帧格式核对 | 按文件大小反推（见测试文档 5.5） |

> **排错黄金法则**：先用官方 sample 跑通 → 再改自己的代码。sample 能过而你的不过，问题在你的代码；sample 也不过，问题在配置/资源。

---

## 10. 上手路径建议

| 阶段 | 目标 | 动作 |
|------|------|------|
| 1 | 建立基线 | 跑通 `sensor_test` / `sample_venc` |
| 2 | 读代码 | `sample_vio/main.c` → `sample_common_vi.c` |
| 3 | 读懂实现 | `modules/vpu/src/cvi_vi.c` |
| 4 | 改配置 | 修改 VB 块大小、分辨率，观察行为变化 |
| 5 | 写自己的 app | 复制 sample 目录，替换 `main.c` |
| 6 | 加业务 | 取帧 → OpenCV / TPU 推理 → 输出 |

---

## 11. 参考资料

| 资料 | 链接 |
|------|------|
| CVITEK MPI 文档（VB 调试） | <https://doc.sophgo.com/cvitek-develop-docs/master/docs_latest_release/CV180x_CV181x/en/01.software/MPI/Media_Processing_Software_Development_Reference/build/html/12_Proc_Debugging_Information_Explanation/VB.html> |
| CVITEK MIPI 用户指南 | <https://doc.sophgo.com/cvitek-develop-docs/master/docs_latest_release/CV186x/en/01.software/MPI/MIPI_User_Guide/build/MIPIUserGuide_en.pdf> |
| Cvitek 参考文档汇总 | <https://sophgocommunity.github.io/SG2042-Wiki/docs/duo/resources/mmf/> |
| LicheeRV-Nano-Build 仓库 | <https://github.com/sipeed/LicheeRV-Nano-Build> |
| 相机相关 Issue #97 | <https://github.com/sipeed/LicheeRV-Nano-Build/issues/97> |

---

## 12. 最重要的一句话

> **先用 SDK 自带 sample 跑通，再复制它改自己的代码。**
>
> MMF 的坑几乎都集中在 **VB 池大小** 和 **资源未释放** 两件事上；把 sample 当模板能绕开 90% 的问题。
