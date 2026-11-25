# MMF + OpenCV + NPU 开发全流程（rmdrone）

> 目标：用 CVI MMF 采集 GC4653 图像，经 VPSS 分流成两路——
> 一路 360×360 三平面给 NPU，一路 1280×720 BGR 给 OpenCV。
>
> 本文记录**从零创建、编译、部署、运行到踩坑**的完整过程。
>
> 配套文档：
> [01-构建镜像.md](01-构建镜像.md) ｜ [02-GC4653摄像头测试.md](02-GC4653摄像头测试.md) ｜ [03-MMF开发指南.md](03-MMF开发指南.md)

---

## 1. 最终架构

```
                        ┌─ CHN0 ─ 360×360  RGB_888_PLANAR ──▶ NPU（零拷贝三平面）
GC4653 ─▶ VI ─▶ ISP ─▶ VPSS
 2560×1440   (NV21)    grp0
                        └─ CHN1 ─ 1280×720 BGR_888 ───────▶ cv::Mat（零拷贝）
```

| 环节 | 格式 | 分辨率 | 说明 |
|------|------|--------|------|
| sensor 输出 | RAW12 | 2560×1440 | GC4653 固定模式 |
| VI/ISP 输出 | **NV21** | 2560×1440 | `IniToViCfg` 默认 |
| VPSS 组输入 | **NV21** | 2560×1440 | **必须与 VI 一致** |
| VPSS CHN0 | RGB_888_PLANAR | 360×360 | 给 NPU |
| VPSS CHN1 | BGR_888 | 1280×720 | 给 OpenCV |

**实测性能**：27.3 fps，帧间隔均值 36.7 ms。

---

## 2. 准备工作

### 2.1 目录位置

```
LicheeRV-Nano-Build/
└── middleware/v2/sample/
    └── rmdrone/               ← 新建
        ├── main.cpp
        └── Makefile
```

**为什么放这里**：

- 同级有 `../common/`（`sample_comm.h` 与 `SAMPLE_COMM_*` 实现）
- 有 `../../Makefile.param`（交叉编译参数）
- `sample/Makefile` 用 `find` 自动发现子目录，**新增目录不用注册**

```bash
mkdir -p middleware/v2/sample/rmdrone
```

### 2.2 OpenCV 包的关键特性（踩坑后才明白）

你用的包：`opencv-mobile-5.0.0-licheerv-nano`

```bash
ls $OCV/lib/
# libopencv_core.a      14M
# libopencv_features.a  2.8M
# libopencv_geometry.a  7.2M
# libopencv_highgui.a   1.4M
# libopencv_imgproc.a    15M
# libopencv_photo.a     5.4M
# libopencv_video.a     2.3M
```

**三个必须知道的事实**：

| 事实 | 影响 |
|------|------|
| **没有 `imgcodecs`** | `cv::imwrite` / `cv::imread` **不可用** → 自己写 PPM |
| 头文件在 **`include/opencv5/`** 下 | `-I` 必须指到 `include/opencv5`，不是 `include` |
| 没有 `calib3d` | `cv::drawFrameAxes` 依赖的 `projectPoints` 缺失 → 避免用 `cv::line` 或补 stub |

验证：

```bash
OCV=$HOME/Desktop/study/RMDrone/sources/opencv-mobile-5.0.0-licheerv-nano
ls $OCV/include/              # 应只有 opencv5
ls $OCV/include/opencv5/opencv2/opencv.hpp   # 应存在
file $OCV/lib/libopencv_core.a | head -2     # 应为 RISC-V
```

---

## 3. 核心数据结构

### 3.1 尺寸

```c
typedef struct _SIZE_S {
    CVI_U32 u32Width;
    CVI_U32 u32Height;
} SIZE_S;
```

### 3.2 VB 池配置

```c
#define VB_MAX_COMM_POOLS   (16)

typedef struct _VB_POOL_CONFIG_S {
    CVI_U32         u32BlkSize;    // 每块字节数
    CVI_U32         u32BlkCnt;     // 块数量
    VB_REMAP_MODE_E enRemapMode;   // 一般用 VB_REMAP_MODE_NONE
    CVI_CHAR        acName[MAX_VB_POOL_NAME_LEN];  // 调试名
} VB_POOL_CONFIG_S;

typedef struct _VB_CONFIG_S {
    CVI_U32          u32MaxPoolCnt;                 // 实际用几个池
    VB_POOL_CONFIG_S astCommPool[VB_MAX_COMM_POOLS];// 16 个槽位
} VB_CONFIG_S;
```

**注意**：结构体里**没有"给谁用"的字段** —— 驱动按「块大小 ≥ 请求大小」自动匹配池。

### 3.3 帧描述

```c
typedef struct _VIDEO_FRAME_S {
    CVI_U32        u32Width;
    CVI_U32        u32Height;
    PIXEL_FORMAT_E enPixelFormat;
    CVI_U64        u64PhyAddr[3];   // 物理地址（硬件用）
    CVI_U8        *pu8VirAddr[3];   // 虚拟地址（可能为 NULL！）
    CVI_U32        u32Stride[3];    // 每行字节数（≠ width）
    CVI_U32        u32Length[3];    // 每平面字节数
} VIDEO_FRAME_S;

typedef struct _VIDEO_FRAME_INFO_S {
    VIDEO_FRAME_S stVFrame;    // 帧本体
    CVI_U32       u32PoolId;   // 来自哪个 VB 池（Release 要用）
} VIDEO_FRAME_INFO_S;
```

### 3.4 VPSS 组与通道

```c
typedef struct _VPSS_GRP_ATTR_S {
    CVI_U32           u32MaxW;        // 组能处理的最大宽
    CVI_U32           u32MaxH;
    PIXEL_FORMAT_E    enPixelFormat;  // ★ 输入格式，必须等于 VI 输出
    FRAME_RATE_CTRL_S stFrameRate;
    CVI_U8            u8VpssDev;
} VPSS_GRP_ATTR_S;

typedef struct _VPSS_CHN_ATTR_S {
    CVI_U32           u32Width;        // 输出宽
    CVI_U32           u32Height;
    VIDEO_FORMAT_E    enVideoFormat;   // VIDEO_FORMAT_LINEAR
    PIXEL_FORMAT_E    enPixelFormat;   // ★ 输出格式
    FRAME_RATE_CTRL_S stFrameRate;
    CVI_BOOL          bMirror;
    CVI_BOOL          bFlip;
    CVI_U32           u32Depth;        // 队列深度
    ASPECT_RATIO_S    stAspectRatio;   // 拉伸 vs 保持比例
    VPSS_NORMALIZE_S  stNormalize;     // 硬件归一化（给 NPU 能省 CPU）
} VPSS_CHN_ATTR_S;
```

### 3.5 模块端点（用于绑定）

```c
typedef struct _MMF_CHN_S {
    MOD_ID_E enModId;   // CVI_ID_VI / CVI_ID_VPSS / CVI_ID_VENC ...
    CVI_S32  s32DevId;  // 设备号（VPSS 场景下 = 组号）
    CVI_S32  s32ChnId;  // 通道号
} MMF_CHN_S;
```

### 3.6 VI 配置（SDK 封装）

```c
typedef struct _SAMPLE_CHN_INFO_S {
    VI_CHN          ViChn;
    PIXEL_FORMAT_E  enPixFormat;      // 实测为 NV21
    DYNAMIC_RANGE_E enDynamicRange;
    VIDEO_FORMAT_E  enVideoFormat;
    COMPRESS_MODE_E enCompressMode;
    // ⚠️ 没有 stSize 字段！
} SAMPLE_CHN_INFO_S;

typedef struct _SAMPLE_VI_INFO_S {
    SAMPLE_SENSOR_INFO_S stSnsInfo;
    SAMPLE_DEV_INFO_S    stDevInfo;
    SAMPLE_PIPE_INFO_S   stPipeInfo;
    SAMPLE_CHN_INFO_S    stChnInfo;
    SAMPLE_SNAP_INFO_S   stSnapInfo;
} SAMPLE_VI_INFO_S;

typedef struct _SAMPLE_VI_CONFIG_S {
    CVI_S32           s32WorkingViNum;
    CVI_S32           as32WorkingViId[...];
    SAMPLE_VI_INFO_S  astViInfo[...];
} SAMPLE_VI_CONFIG_S;
```

### 3.7 像素格式（本项目用到的）

```c
PIXEL_FORMAT_RGB_888 = 0,
PIXEL_FORMAT_BGR_888,          // 1  CHN1 输出
PIXEL_FORMAT_RGB_888_PLANAR,   // 2  CHN0 输出
PIXEL_FORMAT_BGR_888_PLANAR,   // 3
PIXEL_FORMAT_RGB_BAYER_12BPP,  // 10 RAW12
PIXEL_FORMAT_NV12,             // 18
PIXEL_FORMAT_NV21,             // 19  VI/VPSS 组用这个
```

---

## 4. 程序逻辑

### 4.1 初始化顺序（不能乱）

```
① vi_prepare()      解析 ini → VI 配置 → 查尺寸
② vb_pool_init()    按尺寸建三档池
③ CVI_SYS_Init()
④ vi_start()        SAMPLE_PLAT_VI_INIT → sensor/MIPI/Dev/Chn/ISP
⑤ vpss_start()      CreateGrp → SetChnAttr×2 → EnableChn×2 → StartGrp
⑥ bind_vi_vpss()    CVI_SYS_Bind(VI → VPSS)
⑦ 主循环            双通道 GetFrame → 处理 → ReleaseFrame
⑧ 逆序清理          UnBind → StopGrp → DisableChn → DestroyGrp
                    → DestroyIsp/DestroyVi → SYS_Exit → VB_Exit
```

**为什么 ① 在 ② 之前**：VB 池的块大小要按分辨率算，必须先知道尺寸。

### 4.2 三档 VB 池（关键设计）

```
pool0  pnpu   389,120 × 5  → 360×360 RGB PLANAR 输出
pool1  pcv  2,764,800 × 4  → 1280×720 BGR 输出
pool2  pvi  5,529,600 × 4  → VI 采集 RAW12/NV21
                合计 ≈ 34 MB（ION 预留 105 MB）
```

**块大小计算公式**：

| 用途 | 公式 | 1440p 值 |
|------|------|----------|
| RAW12 / NV21 | `W × H × 3 / 2` | 5,529,600 |
| RGB/BGR 888 | `W × H × 3` | 5,529,600（720p 时 2,764,800） |

**顺序必须从小到大**：驱动按「第一个满足大小」匹配池，大池在前会让小请求吃掉大块。

### 4.3 双通道主循环

```c
while (!g_stop) {
    // NPU 通道
    if (CVI_VPSS_GetChnFrame(grp, NPU_CHN, &f, 1000) == CVI_SUCCESS) {
        process_npu_frame(f, idx_npu++);
        CVI_VPSS_ReleaseChnFrame(grp, NPU_CHN, &f);   // 必须
    }
    // CV 通道
    if (CVI_VPSS_GetChnFrame(grp, CV_CHN, &f, 1000) == CVI_SUCCESS) {
        process_cv_frame(f, idx_cv++);
        CVI_VPSS_ReleaseChnFrame(grp, CV_CHN, &f);    // 必须
    }
}
```

### 4.4 零拷贝的两种接法

**NPU 侧（三平面）**

```c
CVI_U8 *pr = m.get(vf, 0);   // R 平面
CVI_U8 *pg = m.get(vf, 1);   // G
CVI_U8 *pb = m.get(vf, 2);   // B
// 逐平面填入 tensor（NCHW: [1,3,H,W]）
```

**OpenCV 侧（交错 BGR）**

```c
cv::Mat img(h, w, CV_8UC3, p, st);   // st = u32Stride[0]
```

**`step` 必须是 stride，不是 `w*3`** —— 否则图像斜切。

### 4.5 FrameMap（RAII 地址映射）

```c
struct FrameMap {
    void   *p[3];
    CVI_U32 len[3];
    bool    mapped[3];

    ~FrameMap() {                     // 析构自动 unmap
        for (int i = 0; i < 3; ++i)
            if (mapped[i] && p[i]) CVI_SYS_Munmap(p[i], len[i]);
    }

    CVI_U8 *get(const VIDEO_FRAME_S &vf, int i) {
        if (vf.pu8VirAddr[i]) return vf.pu8VirAddr[i];  // 驱动已给
        if (vf.u64PhyAddr[i] == 0) return nullptr;
        CVI_U32 sz = vf.u32Length[i];
        if (sz == 0) sz = vf.u32Stride[i] * vf.u32Height;
        void *vir = CVI_SYS_Mmap(vf.u64PhyAddr[i], sz); // 2 参数，返回指针
        ...
    }
};
```

> ⚠️ `CVI_SYS_Mmap(phy, size)` 只有 **2 个参数**且**直接返回 `void*`**，
> 不是早期 SDK 的 `(phy, size, &vir, cache)` 四参数版本。

### 4.6 存图（因无 imgcodecs，自己写 PPM）

```c
static bool write_bgr_ppm(const cv::Mat &bgr, const char *path)
{
    FILE *fp = fopen(path, "wb");              // "wb" 二进制模式
    fprintf(fp, "P6\n%d %d\n255\n", w, h);     // PPM 头
    for (int y = 0; y < h; ++y) {
        const cv::Vec3b *src = bgr.ptr<cv::Vec3b>(y);
        for (int x = 0; x < w; ++x) {
            row[x*3+0] = src[x][2];   // R ← BGR 第 3 字节
            row[x*3+1] = src[x][1];   // G
            row[x*3+2] = src[x][0];   // B
        }
        fwrite(row.data(), 1, row.size(), fp);
    }
}
```

### 4.7 调试开关

```c
#define DBG_SAVE_JPG        1     // 存 PPM
#define DBG_SAVE_EVERY_N    30    // 每 N 帧一张
#define DBG_SAVE_W          640   // 存图宽度（0=原尺寸）
#define DBG_DUMP_NPU_PPM    1     // 导出 NPU 输入 PPM
#define DBG_LOG_FPS         1     // 打印帧率
#define DBG_LOG_EVERY_N     30
#define DBG_DRAW_CROSS      0     // 画十字（会触发 calib3d 依赖，见 8.3）
```

---

## 5. 编译

### 5.1 方式 A：手写 g++ 命令（最可靠，推荐首次使用）

```bash
SDK=$HOME/Desktop/study/RMDrone/sources/LicheeRV-Nano-Build
OCV=$HOME/Desktop/study/RMDrone/sources/opencv-mobile-5.0.0-licheerv-nano

cd $SDK/middleware/v2/sample/rmdrone

$SDK/host-tools/gcc/riscv64-linux-musl-x86_64/bin/riscv64-unknown-linux-musl-g++ \
  main.cpp -o rmdrone \
  -I$SDK/middleware/v2/include \
  -I$SDK/middleware/v2/modules/isp/include/sg200x \
  -I../common \
  -I$SDK/linux_5.10/build/sg2002_licheervnano_sd/riscv/usr/include \
  -I$OCV/include/opencv5 \
  -std=c++11 -fpermissive -O2 \
  -L$OCV/lib \
  -Wl,--start-group -lopencv_imgproc -lopencv_core \
    $(PKG_CONFIG_PATH=$SDK/middleware/v2/pkgconfig pkg-config --libs \
        --define-variable=mw_dir=$SDK/middleware/v2 cvi_common cvi_sample) \
    -lsys \
  -Wl,--end-group \
  -lpthread -lm -lini \
  -Wl,-rpath,/mnt/system/usr/lib \
  -Wl,-rpath,/mnt/system/usr/lib/3rd
```

**四个关键点**：

| 点 | 说明 |
|----|------|
| `-I$OCV/include/opencv5` | 不是 `$OCV/include` |
| `--start-group ... --end-group` | 解决静态库交叉依赖（imgproc ↔ core ↔ sample） |
| `-Wl,-rpath,...` 两个路径 | 让二进制自己找到 MMF 库，不用设环境变量 |
| `-lini` | `libsample.so` 运行时需要 |

**产物**：

```
-rwxr-xr-x  1002K  rmdrone
ELF 64-bit LSB executable, UCB RISC-V, RVC, double-float ABI
interpreter /lib/ld-musl-riscv64xthead.so.1
```

### 5.2 方式 B：Makefile（日常用）

```makefile
SHELL = /bin/bash

ifeq ($(PARAM_FILE), )
	PARAM_FILE:=../../Makefile.param
	include $(PARAM_FILE)
endif
include ../sample.mk

# ----------------------------- OpenCV -----------------------------
OPENCV_ROOT ?= $(HOME)/Desktop/study/RMDrone/sources/opencv-mobile-5.0.0-licheerv-nano
OPENCV_INC   = $(OPENCV_ROOT)/include/opencv5      # ★ 注意 opencv5
OPENCV_LIB   = $(OPENCV_ROOT)/lib
OPENCV_LIBS  = -Wl,--start-group -lopencv_imgproc -lopencv_core -Wl,--end-group
# ------------------------------------------------------------------

SDIR = $(CURDIR)                                   # ★ 必须 CURDIR，不能 PWD
SRCS = $(wildcard $(SDIR)/*.cpp)
INCS = -I$(MW_INC) -I$(ISP_INC) -I../common/ -I$(KERNEL_INC) -I$(OPENCV_INC)
OBJS = $(SRCS:.cpp=.o)
DEPS = $(SRCS:.cpp=.d)

TARGET = rmdrone

PKG_CONFIG_PATH = $(MW_PATH)/pkgconfig
REQUIRES = cvi_common cvi_sample
MW_LIBS = $(shell PKG_CONFIG_PATH=$(PKG_CONFIG_PATH) pkg-config --libs \
            --define-variable=mw_dir=$(MW_PATH) $(REQUIRES))

LIBS = $(MW_LIBS)

CXXSTD  = -std=c++11
CXXONLY = -fpermissive -Wno-write-strings

EXTRA_CFLAGS  = $(INCS) $(DEFS)
EXTRA_LDFLAGS = $(LIBS) -lpthread -lm -lini -L$(OPENCV_LIB) $(OPENCV_LIBS) \
                -Wl,-rpath,/mnt/system/usr/lib \
                -Wl,-rpath,/mnt/system/usr/lib/3rd

.PHONY : clean all
all: $(TARGET)

$(SDIR)/%.o: $(SDIR)/%.cpp
	@$(CXX) $(DEPFLAGS) $(CFLAGS) $(CXXSTD) $(CXXONLY) $(EXTRA_CFLAGS) -o $@ -c $<
	@echo [$(notdir $(CXX))] $(notdir $@)

$(TARGET): $(OBJS) $(ISP_OBJ) $(MW_LIB)/libvpu.a $(MW_LIB)/libsys.a
	@$(CXX) -o $@ -Wl,--start-group $(OBJS) -lsys $(MW_LIB)/libsys.a -Wl,--end-group \
	        $(ELFFLAGS) $(EXTRA_LDFLAGS)
	@echo -e $(BLUE)[LINK]$(END)[$(notdir $(CXX))] $(notdir $@)

clean:
	@rm -f $(OBJS) $(DEPS) $(TARGET)

-include $(DEPS)
```

**用法**：

```bash
cd ~/Desktop/study/RMDrone/sources/LicheeRV-Nano-Build
source build/cvisetup.sh
defconfig sg2002_licheervnano_sd
cd middleware/v2/sample/rmdrone      # ★ 一定要 cd 进去（PWD 才正确）
make clean && make
```

---

## 6. 部署

### 6.1 拷贝到板子

```bash
cd ~/Desktop/study/RMDrone/sources/LicheeRV-Nano-Build
scp middleware/v2/sample/rmdrone/rmdrone root@licheervnano-8050.local:/root/
```

> 板子地址：`10.128.80.1` / `10.128.81.1` / `licheervnano-8050.local`，密码 `root`

### 6.2 设置库路径（两种方式）

**方式 A：环境变量**

```bash
export LD_LIBRARY_PATH=/mnt/system/usr/lib:/mnt/system/usr/lib/3rd
```

**方式 B：链接时写 rpath（推荐，已包含在上面命令里）**

```bash
-Wl,-rpath,/mnt/system/usr/lib -Wl,-rpath,/mnt/system/usr/lib/3rd
```

验证：

```bash
riscv64-unknown-linux-musl-readelf -d rmdrone | grep -i runpath
```

### 6.3 **必须 reboot**

```bash
ssh root@licheervnano-8050.local
reboot
```

> MMF 的 VB 池 / VI / ISP 是**全局独占资源**，程序异常退出不释放。
> 不 reboot 会看到 `vb has already inited, set_config cmd has no effect`。

### 6.4 运行

重连后：

```bash
cat /proc/cvitek/vb
# 必须显示：vb_pool has not inited yet

chmod +x /root/rmdrone
/root/rmdrone
```

### 6.5 停止

`Ctrl+C` → 触发 `on_signal` → `g_stop = 1` → 主循环退出 → 执行完整清理。

**不要用 `kill -9`** —— 会跳过清理，VB 池残留，必须 reboot。

---

## 7. 运行结果与验证

### 7.1 正常日志

```
==== rmdrone start ====
[CFG] devNum=1 snsType0=18 size=2560x1440
[VB] pnpu  5 x  389120 =  1900 KB
[VB] pcv   4 x 2764800 = 10800 KB
[VB] pvi   4 x 5529600 = 21600 KB
[VB] total = 34300 KB
===GC4653 1440P 30fps 10bit LINEAR Init OK!===
[VI] started
[VPSS] grp0: CHN0=360x360 PLANAR | CHN1=1280x720 BGR
[SYS] VI(0,0) -> VPSS(0,0)
[RUN] Ctrl+C 退出
[NPU    30] 360x360 stride=384/384/384 px0=(70,60,68)
[CV     30] 1280x720 st=3840 | 间隔 33.33 ms | 均值 36.66 ms (27.3 fps)
[CV     30] saved /mnt/data/cv_00030.ppm (write 45.4 ms, 675 KB)
```

### 7.2 指标解读

| 日志 | 含义 | 正常值 |
|------|------|--------|
| `[CFG] size=2560x1440` | sensor 尺寸识别 | 必须 2560×1440 |
| `[VB] total` | 池总占用 | 34 MB（< 105 MB） |
| `Init OK!` | sensor 寄存器下发成功 | 必须有 |
| `[NPU] px0=(...)` | 三平面首像素 | 非全 0（AE 收敛后） |
| `[CV] st=3840` | stride | 1280×3 = 3840，无填充 |
| `均值 33~37 ms` | 帧间隔 | 27~30 fps |

### 7.3 取图验证

文件在板子 `/mnt/data/`：

```bash
# 板上
ls -lh /mnt/data/*.ppm

# 虚拟机
scp root@licheervnano-8050.local:/mnt/data/npu_input.ppm ~/
scp root@licheervnano-8050.local:/mnt/data/cv_00390.ppm  ~/

# 转 PNG
ffmpeg -y -i ~/npu_input.ppm ~/npu.png
ffmpeg -y -i ~/cv_00390.ppm  ~/cv.png
xdg-open ~/cv.png
```

### 7.4 清理板子空间

```bash
rm -f /mnt/data/cv_*.ppm /mnt/data/npui*.ppm
```

---

## 8. Bug 全记录（按发生阶段）

### 8.1 编译期

#### ❌ `undefined reference to 'main'`

**现象**：

```
crt1.o: in function `_start_c': undefined reference to `main'
```

**根因**：`SDIR = $(PWD)` 在 `make -C` 下失效。

`$(PWD)` 是环境变量（执行 make 的目录），`make -C` 只改 make 的工作目录，**不改 `PWD`** → `wildcard` 扫到空目录 → `OBJS` 空 → 没有 main。

**修法**：

```makefile
SDIR = $(CURDIR)     # CURDIR 是 make 内置变量，-C 之后会自动更新
```

或者编译前 `cd` 进目录，用 `make`（不是 `make -C`）。

---

#### ❌ `opencv2/opencv.hpp: No such file or directory`

**根因**：头文件在 `include/opencv5/opencv2/`，`-I` 指到了 `include`。

**修法**：

```
-I$(OPENCV_ROOT)/include/opencv5
```

---

#### ❌ `-std=gnu11 is valid for C/ObjC but not for C++`

**根因**：`CFLAGS`（来自 `Makefile.param`）含 `-std=gnu11`，被传给了 `$(CXX)`。

**修法**：把 `-std=c++11` 放在 `$(CFLAGS)` **之后**（后者覆盖前者）：

```makefile
@$(CXX) $(DEPFLAGS) $(CFLAGS) $(CXXSTD) $(CXXONLY) $(EXTRA_CFLAGS) -o $@ -c $<
```

---

#### ❌ `sample_common_vo.c: dsi_hx8394_evb.h: No such file or directory`

**根因**：Makefile 把 `$(COMM_OBJ)`（`../common/*.c` 的 .o）当依赖去编译，但缺少 panel 头文件路径。

**关键认识**：SDK 原版 Makefile 里

```makefile
$(TARGET): $(COMM_OBJ) $(OBJS) ...              # 依赖写 COMM_OBJ
	$(CXX) ... $(OBJS) $(COMM_OBJS) ...         # 链接写 COMM_OBJS（空！）
```

`sample.mk` 只定义 `COMM_OBJ`（单数），`COMM_OBJS` 不存在 → **common 的 .o 被编译但不被链接**。
真正提供实现的是 pkg-config 的 `cvi_sample` 库。

**修法**：不要把 `COMM_OBJ` 放进依赖和链接：

```makefile
$(TARGET): $(OBJS) $(ISP_OBJ) $(MW_LIB)/libvpu.a $(MW_LIB)/libsys.a
	@$(CXX) -o $@ -Wl,--start-group $(OBJS) -lsys $(MW_LIB)/libsys.a -Wl,--end-group ...
```

---

### 8.2 链接期

#### ❌ `cannot find -lopencv_imgcodecs`

**根因**：该 opencv-mobile 包**不含 imgcodecs 模块**（为了精简）。

**修法**：去掉 `-lopencv_imgcodecs`，把 `cv::imwrite` 换成自实现的 PPM 写入。

---

#### ❌ 大量 `undefined reference to cv::softdouble::...` / `cvFloor(...)` / `cv::utils::BufferArea`

**根因**：**静态库链接顺序问题**。`libopencv_imgproc.a` 依赖 `libopencv_core.a`，但 core 在 imgproc 之前被扫描过了。

```
-lopencv_core -lopencv_imgproc
     ↑              ↑
  已扫过        这时才需要 core 的符号 → 找不到
```

**修法**：包进 `--start-group`（让链接器反复扫描）：

```
-Wl,--start-group -lopencv_imgproc -lopencv_core -Wl,--end-group
```

---

#### ❌ `undefined reference to cv::projectPoints(...)`

**根因**：`libopencv_imgproc.a` 里的 `drawing.cpp.o` 同时含 `cv::line`（我们要用）和 `cv::drawFrameAxes`（依赖 calib3d 的 `projectPoints`）。静态库按整个 `.o` 加载 → 需要 `projectPoints`，但包里没 calib3d。

**三种修法**：

| 方案 | 做法 |
|------|------|
| A（最简） | `#define DBG_DRAW_CROSS 0` —— 不用 `cv::line` 就不拉进 `drawing.cpp.o` |
| B（保留画线） | 提供空实现 stub（见下） |
| C（临时） | `-Wl,--unresolved-symbols=ignore-all` |

**方案 B 的 stub**：

```cpp
// stub_calib3d.cpp
#include <opencv2/core.hpp>
namespace cv {
void projectPoints(const InputArray&, const InputArray&, const InputArray&,
                   const InputArray&, const InputArray&,
                   const OutputArray&, const OutputArray&, double) {}
}
```

编译时把 `stub_calib3d.cpp` 一起加进去：

```bash
riscv64-unknown-linux-musl-g++ main.cpp stub_calib3d.cpp -o rmdrone ...
```

---

#### ❌ `undefined reference to SAMPLE_COMM_VI_StartVi` / 找不到 `sample_comm_vi.h`

**根因**：这套 SDK 的 common 头文件是**单一伞形头** `sample_comm.h`（不是 `sample_common.h`，也没有 `sample_comm_vi.h`）。

**修法**：

```cpp
#include "sample_comm.h"
```

---

### 8.3 运行期

#### ❌ `vb has already inited, set_config cmd has no effect`

**根因**：上一次运行（尤其是崩溃/`kill -9`）没走 `CVI_VB_Exit`，VB 池保持 inited。

**修法**：**reboot**。判断：

```bash
cat /proc/cvitek/vb
# 有 PoolId(0) Size(...) → 已被占用
# vb_pool has not inited yet → 干净
```

---

#### ❌ `Error loading shared library libsample.so: No such file or directory`

**根因**：MMF 库在板上 `/mnt/system/usr/lib/`，不在默认搜索路径。

**修法**：

```bash
export LD_LIBRARY_PATH=/mnt/system/usr/lib
```

---

#### ❌ `libini.so` / `libcli.so` 找不到

**根因**：这两个库在**子目录** `3rd` 下。

板上实际位置：

```
/mnt/system/usr/lib/libsample.so        ← 主库
/mnt/system/usr/lib/libsys.so
/mnt/system/usr/lib/3rd/libini.so       ← ini 解析（libsample 依赖）
/mnt/system/usr/lib/3rd/libcli.so       ← CLI（libcvi_audio 依赖）
```

**修法**：

```bash
export LD_LIBRARY_PATH=/mnt/system/usr/lib:/mnt/system/usr/lib/3rd
```

或链接时加两个 rpath：

```
-Wl,-rpath,/mnt/system/usr/lib -Wl,-rpath,/mnt/system/usr/lib/3rd
```

---

#### ❌ `VPSS_CreateGrp failed: 0xc0068003`

**错误码解码**：

```
0xC0068003 = CVI_DEF_ERR(CVI_ID_VPSS=6, EN_ERR_LEVEL_ERROR, 3)
                                                          ↑
                                              EN_ERR_ILLEGAL_PARAM
```

**根因**：`VPSS_GRP_ATTR_S.enPixelFormat` 写成了 `PIXEL_FORMAT_RGB_BAYER_12BPP`（RAW12），
但 **VI/ISP 输出的是 NV21**，格式不匹配。

**修法**：

```c
grp.enPixelFormat = PIXEL_FORMAT_NV21;   // 必须与 VI 通道输出一致
```

判断方法（看 SDK 样例）：

```bash
grep -n "enPixFormat" middleware/v2/sample/vio/sample_vio.c | head
# 125: PIXEL_FORMAT_E enPixFormat1 = PIXEL_FORMAT_NV21;
# 154: stViConfig...stChnInfo.enPixFormat = enPixFormat1;
# 235: stVpssGrpAttr.enPixelFormat      = enPixFormat1;
```

---

#### ❌ `No valid pool for size(N)`

**根因**：VB 池里没有块大小 ≥ N 的池，或块用完了。

**排查**：

```bash
cat /proc/cvitek/vb        # 看各池 BlkSz / Free
```

**修法**：

- 块太小 → 调大 `u32BlkSize`（按 4.2 的公式）
- 块不够 → 增加 `u32BlkCnt`
- 顺序错 → 池要从**小到大**排列

---

#### ❌ 图像斜切 / 右侧花屏

**根因**：`cv::Mat` 的 `step` 传了 `width*3`，而实际 stride 更大。

**修法**：

```c
cv::Mat img(h, w, CV_8UC3, p, vf.u32Stride[0]);   // 必须用 stride
```

**本例实测** stride = 3840（1280×3），恰好无填充；但 360 宽的 NPU 通道 stride = 384（≠360），所以**不能写死 `w*3`**。

---

#### ❌ 程序退出后 CPU 占用不降 / 下次启动失败

**根因**：没走完整清理。

**修法**：确认 `Ctrl+C` 触发的是信号处理而非 `kill -9`；保证 `unbind → StopGrp → DisableChn → DestroyGrp → DestroyVi → SYS_Exit → VB_Exit` 全部执行。

---

#### ⚠️ NPU 首帧像素 `(0,0,0)`

**不是 bug** —— AE/AWB 还在收敛。几十帧后恢复正常（实测第 30 帧变成 `(70,60,68)`）。

---

#### ⚠️ 帧率只有 27 fps 而非 30

**原因**：

1. 主循环**串行**处理两个通道
2. 存图帧要 40~70 ms 写 PPM

**优化**：

```c
#define DBG_SAVE_JPG  0     // 关掉存图
```

或者改双线程。

---

## 9. 性能数据

| 指标 | 实测 |
|------|------|
| 帧率（开存图） | 27.3 fps |
| 帧间隔均值 | 36.7 ms |
| CV 分辨率 / stride | 1280×720 / 3840 |
| NPU 分辨率 / stride | 360×360 / 384×3 |
| VB 总占用 | 34 MB / 105 MB |
| 二进制大小 | 1002 KB |
| PPM 写入耗时 | 38~70 ms（640×360，675 KB） |

---

## 10. 后续扩展

### 10.1 接 NPU 推理

在 `process_npu_frame` 的 TODO 处：

```c
// cviruntime 方式
CVI_TENSOR *in = CVI_NN_GetInputTensor(model);
memcpy(CVI_NN_TensorPtr(in) + 0*360*360, pr, 360*384);   // R
memcpy(CVI_NN_TensorPtr(in) + 1*360*360, pg, 360*384);   // G
memcpy(CVI_NN_TensorPtr(in) + 2*360*360, pb, 360*384);   // B
CVI_NN_Forward(model, inputs, 1, outputs, n);
```

> 注意 stride 384 ≠ 360，逐行拷贝或先紧凑化。

### 10.2 改双线程

```c
void *npu_thread(void *) { while (!g_stop) { Get/Process/Release; } }
void *cv_thread(void *)  { while (!g_stop) { Get/Process/Release; } }
```

### 10.3 用硬件归一化

```c
c0.stNormalize.bEnable = CVI_TRUE;
c0.stNormalize.afMean[0] = 0.485f; ...   // ImageNet 均值
c0.stNormalize.afStd[0]  = 0.229f; ...
```

VPSS 直接输出归一化结果，NPU 侧省一次 CPU 遍历。

### 10.4 固化到镜像

```bash
cp middleware/v2/sample/rmdrone/rmdrone \
   install/soc_sg2002_licheervnano_sd/rootfs/root/
pack_burn_image
```

---

## 11. 速查表

### 11.1 路径

| 内容 | 路径 |
|------|------|
| 源码 | `middleware/v2/sample/rmdrone/` |
| 头文件（MMF） | `middleware/v2/include/` |
| 头文件（ISP） | `middleware/v2/modules/isp/include/sg200x/` |
| 头文件（内核 UAPI） | `linux_5.10/build/sg2002_licheervnano_sd/riscv/usr/include/linux/` |
| 工具链 | `host-tools/gcc/riscv64-linux-musl-x86_64/bin/` |
| OpenCV 头 | `$OCV/include/opencv5/` |
| OpenCV 库 | `$OCV/lib/` |
| 板上库 | `/mnt/system/usr/lib/` + `/mnt/system/usr/lib/3rd/` |
| 板上输出 | `/mnt/data/` |

### 11.2 关键 API

| 功能 | API |
|------|-----|
| 建 VB 池 | `CVI_VB_SetConfig` → `CVI_VB_Init` |
| 系统初始化 | `CVI_SYS_Init` |
| 解析 ini | `SAMPLE_COMM_VI_ParseIni` → `SAMPLE_COMM_VI_IniToViCfg` |
| 启动 VI | `SAMPLE_PLAT_VI_INIT` |
| 建 VPSS 组 | `CVI_VPSS_CreateGrp` → `SetChnAttr` → `EnableChn` → `StartGrp` |
| 绑定 | `CVI_SYS_Bind` |
| 取帧 | `CVI_VPSS_GetChnFrame` / `ReleaseChnFrame` |
| 地址映射 | `CVI_SYS_Mmap(phy, size)` → `void*` |
| 释放 VI | `SAMPLE_COMM_VI_DestroyIsp` → `DestroyVi` |
| 释放系统 | `SAMPLE_COMM_SYS_Exit` → `CVI_VB_Exit` |

### 11.3 排查命令

```bash
# 池状态
cat /proc/cvitek/vb

# 运行依赖
ldd /root/rmdrone | grep "not found"

# rpath
riscv64-unknown-linux-musl-readelf -d rmdrone | grep -i runpath

# 内核日志
dmesg | grep -i "vpss\|vi\|isp\|vb"

# 空间
df -h /mnt/data
```

---

## 12. 一句话总结

> **三件事决定成败**：
> 1. **VPSS 组输入格式必须等于 VI 输出**（NV21）→ 否则 `0xC0068003`
> 2. **静态库必须 `--start-group`**（imgproc ↔ core ↔ sample）→ 否则一堆 `undefined reference`
> 3. **运行时库路径必须含 `3rd` 子目录** → 否则 `libini.so` 找不到
>
> 加上**每次崩溃后 reboot**，就能稳定跑通。
