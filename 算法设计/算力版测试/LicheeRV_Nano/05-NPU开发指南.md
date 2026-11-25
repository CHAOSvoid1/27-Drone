# LicheeRV Nano NPU 开发指南（SG2002 / CVI MMF）

> 本文说明如何把 VPSS 的 RGB 平面帧送入 SG2002 NPU，并接入模型加载、输入预处理、推理和输出解析。
>
> 当前 `sim/code/rmdrone/main.cpp` 已完成 GC4653 → VI → VPSS → NPU 输入链路。`process_npu_frame()` 中的模型推理仍是 TODO，运行时 API 和库路径需按实际 SDK 版本核对。

## 1. 数据流

```text
GC4653 → VI / ISP → VPSS
                    ├─ CHN0 360×360 RGB_888_PLANAR → NPU
                    └─ CHN1 1280×720 BGR           → OpenCV
```

NPU 通道是三平面图像，分别保存 R、G、B。实际 stride 通常大于宽度，模型要求紧凑 tensor 时必须逐行复制。

## 2. 当前工程配置

`sim/code/rmdrone/main.cpp` 中的关键配置：

```cpp
#define NPU_CHN  0
#define NPU_W    360
#define NPU_H    360
#define NPU_FMT  PIXEL_FORMAT_RGB_888_PLANAR
#define NPU_DEPTH 4
```

VB 池按三平面大小预留，并做 4 KiB 对齐：

```cpp
const CVI_U32 blk_npu = align_4k(NPU_W * NPU_H * 3);
cfg.astCommPool[0].u32BlkSize = blk_npu;
cfg.astCommPool[0].u32BlkCnt  = POOL_NPU_CNT;
```

修改模型尺寸、格式或通道数时，必须同步修改 VPSS 通道、VB 池和输入转换代码。

## 3. 模型准备

板端使用已经编译好的模型。接入前确认：

| 项目 | 需要确认 |
|------|----------|
| 输入布局 | NCHW 或 NHWC |
| 输入尺寸 | 例如 `1×3×360×360` |
| 数据类型 | `uint8`、`int8` 或 `float32` |
| 通道顺序 | RGB 或 BGR |
| 数值范围 | `0..255`、`0..1` 或量化整数 |
| 量化参数 | scale、zero point、mean/std |
| 输出描述 | 数量、维度、类型和后处理规则 |

不要仅凭文件扩展名判断模型格式，应使用 SDK 的模型检查工具或 sample 查询 tensor 描述。

## 4. 运行时和编译

常见运行时组件包括 `cviruntime`、`cvimath`、`cvikernel`，部分 SDK 还需要 `cnpy`、`glog` 和 `z`。先在 SDK 中查找实际文件：

```bash
find . -iname '*cviruntime*' -o -iname '*cvimath*' -o -iname '*cvikernel*'
find . -iname 'cvi_nn.h' -o -iname 'cviruntime.h'
```

Makefile 中的 `IVE_SUPPORT=1` 分支只是 TPU 链接示例，其中的 `cv1835` 路径不能直接用于 SG2002。应替换为当前 SDK 的实际目录：

```makefile
EXTRA_CFLAGS  += -I$(TPU_PATH)/include
EXTRA_LDFLAGS += -L$(TPU_PATH)/lib \
                 -lcviruntime-static -lcvimath-static \
                 -lcvikernel-static -lcnpy -lglog -lz
```

静态库有交叉依赖时继续使用 `--start-group` 和 `--end-group`。动态库版本则需要同步配置板端库路径。

## 5. 模型生命周期

模型只加载一次，不要在每帧中重复加载：

```text
初始化 VB / SYS / VI / VPSS
→ 加载模型
→ 查询输入输出 tensor
→ 主循环取帧并推理
→ 停止取帧
→ 释放输出、tensor 和模型
→ 逆序释放 VPSS / VI / SYS / VB
```

建议让 `NpuContext` 保存模型句柄、输入 tensor、输出 tensor 和输出数量。具体类型以 SDK 头文件为准。

## 6. 从帧填充输入 tensor

当前代码通过 `FrameMap` 获取三个 plane：

```cpp
CVI_U8 *pr = map.get(vf, 0);
CVI_U8 *pg = map.get(vf, 1);
CVI_U8 *pb = map.get(vf, 2);
```

当输入为紧凑的 NCHW `uint8 [1,3,H,W]` 时，按 stride 逐行复制：

```cpp
static void copy_plane(CVI_U8 *dst, const CVI_U8 *src,
                       CVI_U32 width, CVI_U32 height, CVI_U32 stride)
{
    for (CVI_U32 y = 0; y < height; ++y)
        memcpy(dst + y * width, src + y * stride, width);
}

copy_plane(input + 0 * NPU_W * NPU_H, pr, NPU_W, NPU_H, vf.u32Stride[0]);
copy_plane(input + 1 * NPU_W * NPU_H, pg, NPU_W, NPU_H, vf.u32Stride[1]);
copy_plane(input + 2 * NPU_W * NPU_H, pb, NPU_W, NPU_H, vf.u32Stride[2]);
```

模型要求 BGR 时改成 B、G、R 顺序。模型要求 NHWC 时，需要按像素交错写入，不能直接复制三个平面。

## 7. 归一化和量化

常见 float 输入可以使用：

```cpp
float v = static_cast<float>(pixel) / 255.0f;
v = (v - mean[channel]) / std[channel];
```

量化输入必须使用模型生成工具提供的 scale 和 zero point：

```cpp
int q = static_cast<int>(round(real_value / scale)) + zero_point;
q = std::max(qmin, std::min(qmax, q));
```

输入类型、通道顺序和量化参数必须与模型编译时一致。建议先导出一帧输入，在 PC 端用相同预处理代码对比。

## 8. 推理调用结构

`process_npu_frame()` 应保持以下顺序：

```cpp
取出 pr / pg / pb
→ 获取模型输入 tensor
→ 按 stride 填入 tensor
→ 执行归一化或量化
→ 调用 SDK forward / run 接口
→ 读取输出 tensor
→ 执行模型对应的后处理
```

当前代码里的示例为：

```cpp
CVI_TENSOR *in = CVI_NN_GetInputTensor(model);
// 逐平面填入 tensor（NCHW: [1,3,360,360]）
// CVI_NN_Forward(model, inputs, 1, outputs, n);
```

这些函数名只适用于提供兼容 API 的 SDK 版本。若 SDK 使用另一套 `CVI_NN_*` 接口，应以头文件声明和官方 sample 为准。

## 9. 输出解析

读取输出前先查询每个 tensor 的数量、数据类型、维度、布局和量化参数。不要直接把输出强制转换成 `float*`。量化输出需要反量化：

```cpp
float real_value = (static_cast<int>(q) - zero_point) * scale;
```

检测模型还需要完成框解码、置信度筛选和 NMS。分类模型通常取最大 logit 或概率。后处理应与 PC 端验证脚本保持一致。

## 10. 主循环和资源释放

每次取到 VPSS 帧后必须释放：

```cpp
if (CVI_VPSS_GetChnFrame(VPSS_GRP, NPU_CHN, &frame, 1000) == CVI_SUCCESS) {
    process_npu_frame(frame, frame_index++);
    CVI_VPSS_ReleaseChnFrame(VPSS_GRP, NPU_CHN, &frame);
}
```

推理耗时过长会使 VPSS 队列堆积。应分别测量取帧、推理、后处理和整帧周期，必要时降低通道深度、丢弃旧帧或把采集和推理拆成两个线程。

退出时先停止主循环，再释放模型和 MMF。不要使用 `kill -9`，否则 VB、VI 或 ISP 资源可能残留。

## 11. 编译和部署

```bash
source build/cvisetup.sh
defconfig sg2002_licheervnano_sd
cd middleware/v2/sample/rmdrone
make clean && make
```

部署程序和模型：

```bash
scp rmdrone model.cvimodel root@licheervnano-8050.local:/root/
ssh root@licheervnano-8050.local
export LD_LIBRARY_PATH=/mnt/system/usr/lib:/mnt/system/usr/lib/3rd
/root/rmdrone /root/model.cvimodel
```

若链接时已写入 rpath，可省略环境变量。使用下面命令检查动态库：

```bash
ldd /root/rmdrone | grep 'not found'
```

## 12. 调试和常见问题

先保留 PPM 导出，确认 NPU 输入图像颜色、方向和裁剪正确：

```bash
scp root@licheervnano-8050.local:/mnt/data/npu_input.ppm .
ffmpeg -y -i npu_input.ppm npu_input.png
```

| 现象 | 排查方向 |
|------|----------|
| forward 参数错误 | 输入维度、布局或类型不匹配 |
| 输出全零 | 输入未填充、地址映射错误或量化参数错误 |
| 图像偏色 | RGB/BGR 顺序错误 |
| 图像斜切 | 用了 width 而不是实际 stride |
| 动态库找不到 | 检查 `/mnt/system/usr/lib` 和 `/mnt/system/usr/lib/3rd` |
| 第二次启动失败 | 上次异常退出导致 VB/VI/ISP 未释放 |
| 帧率下降 | 推理和后处理超过采集周期 |

## 13. 性能优化顺序

1. 关闭 PPM 导出和详细日志，测量纯推理耗时。
2. 让 VPSS 直接输出模型需要的尺寸，避免重复 resize。
3. 使用硬件归一化或量化输入，减少 CPU 转换。
4. 对 stride 只做必要的逐行复制。
5. 将采集和推理拆成两个线程，使用有界队列。
6. 最后再调整 VB 块数量、通道深度和线程优先级。

## 14. 完成标准

- 能打印并校验模型输入输出描述。
- NPU 输入帧可导出并正确查看。
- 输入预处理与 PC 端验证脚本一致。
- 连续运行数百帧无 tensor、VB 或内存泄漏。
- 输出结果与 PC 端保持允许的量化误差。
- 正常退出后可以再次启动，无需重启板子。
