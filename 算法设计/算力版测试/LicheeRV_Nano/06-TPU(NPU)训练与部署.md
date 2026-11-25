# 06-TPU(NPU) 训练集训练 · 板端编程 · 实测归档

> 本文把两段内容合并成一份归档：
> **第一部分 `06-TPU(NPU)训练集训练`** —— 从采集到标注、训练、导出 ONNX、量化成 cvimodel；
> **第二部分 `06-TPU(NPU)编程`** —— 板端加载模型、填输入、解析输出、做后处理、释放资源；
> **第三部分 `实际情况`** —— 板端真实运行日志逐行解读（FP32 模型、`Detection count: 0`、6.4 fps）。
>
> 前置文档：[01-构建镜像.md](01-构建镜像.md) ｜ [02-GC4653摄像头测试.md](02-GC4653摄像头测试.md) ｜ [03-MMF开发指南.md](03-MMF开发指南.md) ｜ [04-MMF_CV_NPU.md](04-MMF_CV_NPU.md) ｜ [05-NPU开发指南.md](05-NPU开发指南.md)
>
> **本文与 05 的关系**：05 只写到"把张量填进去"的骨架且明确标注 `TODO`；06 把它走通并记录实测。

---

## 0. 全链路总览

```text
[板端] GC4653 → VI/ISP → VPSS ─┬─ CHN0 384×384 RGB_888_PLANAR ──→ 推理
                               └─ CHN1 1280×720 BGR_888 ──────→ OpenCV 存图/调试
        │
        │ 采集成 PPM/BMP（调试开关，见 main.cpp 的 DBG_*）
        ▼
[PC] 标注（YOLO txt）→ 训练（ultralytics）→ best.pt
        ▼
     导出 ONNX（imgsz=384, opset=12, 不带 NMS）→ best.onnx
        ▼
     TPU-MLIR（Docker: sophgo/tpuc_dev:v3.4）
        model_transform.py → run_calibration.py → model_deploy.py
        ▼
     armor_int8.cvimodel / armor_f32.cvimodel
        ▼
[板端] CVI_NN_* 加载 → 填 f32 输入 → Forward → 解码 + NMS → 打印/画框
```

**关键约定（全链路必须一致，错一个就 0 检测）**

| 项 | 本项目取值 | 说明 |
|----|-----------|------|
| 输入尺寸 | `384×384` | 现模型实测值；旧代码里是 360，必须同步改 |
| 输入名 | `images` | `CVI_NN_GetInputTensor(model, 0)` |
| 输入布局 | NCHW，`Format: 0` | 三平面 RGB 顺序 |
| 输入数值 | float32，`0..1` | 由 `Memory size / Count == 4` 判定（见第三部分） |
| 输出名 | `output0`（板端显示 `output0_Concat_f32`） | 末尾 `_f32` = 未量化 |
| 输出形状 | `[1, 6, 3024]`（板端打印成 `1 6 3024 1`） | `6 = 4 + nc`，`nc = 2` |
| 输出数值 | float32，已激活 `0..1` | **不要**做 int8 反量化 |
| 类别数 | 2 | `armor` 两类（红/蓝或装甲/其它） |

---

# 第一部分 06-TPU(NPU)训练集训练

## 1.1 数据采集

### 来源优先级

| 来源 | 优点 | 缺点 |
|------|------|------|
| **板端 GC4653 实拍**（推荐） | 光照、噪声、畸变、色彩与部署完全一致 | 需人工搬图 |
| 比赛/训练场录屏抽帧 | 数量大、场景多 | 分辨率与镜头不一致 |
| 网络图片 | 补类别 | 域差异大，慎用 |

### 采集方法

板端 `main.cpp` 里已有开关（实际值见文件头）：

```c
#define DBG_SAVE_JPG        1     // 存图到 /mnt/data（本包无 imgcodecs，实际写 PPM）
#define DBG_SAVE_EVERY_N    30    // 每 30 帧存一张
#define DBG_SAVE_W          640   // 存图宽度，0=原尺寸
```

> 实测这行日志每次耗时 **39~44 ms**：
> `[CV 30] saved /mnt/data/cv_00030.ppm (write 44.0 ms, 675 KB)`
> `675 KB ÷ 3 = 230400 px = 640×360`，即存的是**降到 640×360 的 RGB**，不是 1280×720 原图。采集阶段够用，但**校准集最好用原分辨率**，否则量化误差评估会偏乐观。

**搬回 PC**：

```bash
# 板端批量存到 /mnt/data，再整体拉取
scp -r root@192.168.42.1:/mnt/data '*.ppm' ./raw/
```

**PPM → JPG 批量转（PC 端，标注更省事）**：

```bash
sudo apt install -y imagemagick
mogrify -format jpg -quality 92 raw/*.ppm && rm raw/*.ppm
```

### 采集注意事项

1. **同一目标要有远景/近景/遮挡/逆光**，否则装甲板在远距离必然漏检；
2. **负样本**（无装甲的画面：场地、地面、观众）单独留 10~20%，标成空 txt，能明显降误检；
3. **不要只采一个场地**，否则模型学到的是背景不是目标；
4. 每次采集后**立刻**记录：分辨率、镜头、曝光、场地 —— 写进 `dataset/README.md`。

---

## 1.2 标注

### 工具选择

| 工具 | 适用 | 备注 |
|------|------|------|
| **labelImg** | 快速上手 | YOLO 格式一键切换 |
| **X-AnyLabeling** | 半自动（SAM/YOLO 预标） | 效率最高，推荐 |
| **CVAT / Label Studio** | 多人协作、审核流 | 部署重 |

### YOLO 标注格式

```
dataset/
├── images/
│   ├── train/  img_0001.jpg ...
│   └── val/    img_0100.jpg ...
├── labels/
│   ├── train/  img_0001.txt ...
│   └── val/    img_0100.txt ...
└── data.yaml
```

每个 `.txt` 一行一个目标，**归一化**到 `0..1`：

```
<class_id> <cx> <cy> <w> <h>
```

- `class_id` 从 **0** 开始，与训练时 `nc`/`names` 顺序**严格对应**；
- 空文件（无目标的负样本）**必须保留**，不能缺；
- 坐标是**中心点 + 宽高**，不是左上/右下 —— 这是最高频的错标。

### `data.yaml`

```yaml
path: /home/sxb/datasets/armor      # 绝对路径，训练机上的
train: images/train
val: images/val

names:
  0: armor_red
  1: armor_blue
```

> `nc` 不用写，ultralytics 由 `names` 长度推出。**顺序写反 = 类别全错**，且训练指标看不出来。

### 校验脚本（必跑）

```python
# check_labels.py  —— 标完先跑一遍，比训练完发现错强
import glob, os, cv2
BAD = []
for t in glob.glob('dataset/labels/**/*.txt', recursive=True):
    img = t.replace('/labels/', '/images/').replace('.txt', '.jpg')
    if not os.path.exists(img):
        BAD.append(('missing image', t)); continue
    h, w = cv2.imread(img).shape[:2]
    for i, line in enumerate(open(t)):
        p = line.split()
        if len(p) != 5:
            BAD.append((f'line {i+1} fields={len(p)}', t)); continue
        c, cx, cy, bw, bh = int(p[0]), *map(float, p[1:])
        if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < bw <= 1 and 0 < bh <= 1):
            BAD.append((f'line {i+1} out of range', t))
print('bad:', len(BAD))
for b in BAD[:20]:
    print(b)
```

---

## 1.3 训练

### 环境

```bash
conda create -n yolo python=3.10 -y && conda activate yolo
pip install ultralytics
yolo checks          # 确认 CUDA 可用
```

### 训练命令（照抄即可）

```bash
yolo detect train \
  model=yolo11n.pt \
  data=/home/sxb/datasets/armor/data.yaml \
  imgsz=384 \
  epochs=150 \
  batch=32 \
  workers=8 \
  device=0 \
  project=runs/armor \
  name=exp01 \
  patience=50 \
  cache=ram
```

### 关键超参怎么选（针对本项目）

| 参数 | 建议 | 原因 |
|------|------|------|
| `model` | `yolo11n.pt` 或 `yolov8n.pt` | SG2002 只有 ~1 TOPS，**只能上 n** |
| `imgsz` | **384**（与部署一致） | 训练/部署分辨率不一致会掉点；384 是 32 的整数倍且与 NPU 对齐 |
| `epochs` | 100~200 + `patience=50` | 小数据集 300 易过拟合 |
| `batch` | 16~32（看显存） | —— |
| `mosaic` | 前 100 轮 1.0，最后 20 轮 **0**（`close_mosaic=20`） | 装甲板在边缘被切碎会学坏 |
| `degrees` | 0~10 | 装甲板是竖直矩形，大角度旋转反而失真 |
| `fliplr` | **0**（若红蓝是不同类） | 左右翻转会把红蓝语义搞反 |
| `hsv_h/s/v` | 0.015 / 0.7 / 0.4 | 光照变化大，饱和度可大点 |
| `scale` | 0.5 | 模拟远近 |

### 看什么指标判断"能部署"

```bash
tensorboard --logdir runs/armor      # 或看 runs/armor/exp01/results.csv
```

| 指标 | 及格线（装甲板） | 说明 |
|------|-----------------|------|
| `metrics/mAP50` | > 0.90 | 主看 |
| `metrics/mAP50-95` | > 0.65 | 框质量 |
| 各类 P/R | 两类均衡 | 某类 P 高 R 低 = 漏检 |
| `val/box_loss` 与 `train/box_loss` | 差距不拉大 | 拉大 = 过拟合 |

> **部署前必须做“量化前基线”**：用 `best.pt` 在**板端实拍图**上跑一遍，记录 mAP 和单图检测数，这就是后面对比 INT8 掉点的基准。

---

## 1.4 导出 ONNX

```bash
yolo export model=runs/armor/exp01/weights/best.pt \
  format=onnx imgsz=384 opset=12 simplify=True dynamic=False
```

产物：`best.onnx`，**必须核对**：

```bash
python - <<'PY'
import onnx
m = onnx.load('best.onnx')
for i in m.graph.input:
    print('in ', i.name, [d.dim_value for d in i.type.tensor_type.shape.dim])
for o in m.graph.output:
    print('out', o.name, [d.dim_value for d in o.type.tensor_type.shape.dim])
PY
# 期望: in images [1,3,384,384]   out output0 [1,6,3024]   opset 12
```

**`3024` 的来历**（自己会算就不会被坑）：

```
384/8  = 48 → 48² = 2304
384/16 = 24 → 24² =  576
384/32 = 12 → 12² =  144
                合计 3024   ✅
```

**不要**加 `nms=True`：TPU 上 NMS 算子支持差，且板端后处理自己写更可控。

---

## 1.5 量化与 cvimodel 转换

### 准备校准集

```bash
# 从训练集里挑 100~300 张"有代表性"的（别只用最简单的那批）
mkdir -p calib && cp dataset/images/val/*.jpg calib/
ls -1 calib/*.jpg > calib_list.txt
wc -l calib_list.txt
```

**校准集三条规则**：

1. **必须来自真实场景**，不能用 `runs/` 下的预测可视化图；
2. 数量 100~300 足够，多而无代表性反而更差；
3. 校准集的**预处理必须与部署一致**（RGB/BGR、letterbox、归一化）。

### Docker 环境（不用 Docker 可跳过，见 1.6）

```bash
docker load -i tpuc_dev_v3.4.tar.gz
docker run -it --name tpu-mlir \
  -v "$HOME/Desktop/study/RMDrone/sources/armor_deploy":/workspace \
  -w /workspace sophgo/tpuc_dev:v3.4
```

> `--privileged` 官方示例里有，**纯转换不需要**；除非要跑板端仿真。

### 三步转换

```bash
cd /workspace

# ① ONNX → MLIR
model_transform.py \
  --model_name armor \
  --model_def best.onnx \
  --input_shapes [[1,3,384,384]] \
  --mean 0,0,0 --scale 0.0039216 \
  --pixel_format rgb \
  --keep_aspect_ratio \
  --test_input calib/0001.jpg \
  --test_result armor.top_outputs \
  --mlir armor.mlir

# ② 生成量化校准表
run_calibration.py armor.mlir \
  --dataset calib_list.txt \
  --input_num 200 \
  -o armor_calib_table

# ③ 生成 cvimodel（INT8）
model_deploy.py \
  --mlir armor.mlir \
  --quantize INT8 \
  --calibration_table armor_calib_table \
  --chip cv180x \
  --test_input calib/0001.jpg \
  --test_reference armor.top_outputs \
  --tolerance 0.9,0.6 \
  --model armor_int8.cvimodel
```

### ⚠️ `--chip` 到底填 `cv180x` 还是 `cv181x`

| 场景 | 取值 | 依据 |
|------|------|------|
| TPU-MLIR 官方文档 / CV180x 系列 SoC | `cv180x` | 文档默认 |
| **本工程实测可运行的产物** | **`cv181x`** | 板端日志：`armor Build at ... For platform cv181x`，加载运行正常 |
| cvitek-tdl-sdk-sg200x 的 sample 编译 | `CHIP=CV181X` | Milk-V Duo 256M（SG2002）官方示例即如此 |

**结论：以实测为准。** 本链路 SG2002 + cvi runtime 1.4.0 跑的是 **cv181x 构建** 的 cvimodel；
05 文档里写的 `--chip cv180x` 属于 TPU-MLIR 通用口径，**遇到 `CVI_NN_RegisterModel` 失败时先换 `cv181x` 重试**。

### 怎么验证转换没坏

```bash
# 量化前后逐层/逐输出误差都打印在 model_deploy 日志里
# 另存一份 FP32 做对照（慢但准）
model_deploy.py --mlir armor.mlir --quantize F32 --chip cv181x \
  --model armor_f32.cvimodel
```

实测日志显示当前板上跑的就是 **FP32**（`Memory size / Count = 4`），推理慢的根因在此 —— 详见第三部分。

---

## 1.6 不用 Docker 的转换（可选）

TPU-MLIR 有 pip 包（版本需与 SDK 对齐，否则 cvimodel 不兼容）：

```bash
python3 -m venv .venv-tpumlir && . .venv-tpumlir/bin/activate
pip install tpu_mlir        # 或从发布包 install
model_transform.py --help   # 能打印 help 才算装对
```

**风险**：pip 版本与板端 `libcviruntime` 版本不匹配时，`RegisterModel` 会直接失败（模型版本号对不上）。
**实践建议**：转换还是放 Docker 里做，**运行时和转换时的版本一致**是最省事的。

---

## 1.7 训练阶段踩坑清单

| 现象 | 原因 | 处理 |
|------|------|------|
| 训练 mAP 很高，板端 0 检测 | 预处理不一致（BGR/RGB、归一化、letterbox） | 用同一张图在 PC 和板上各跑一次对比 |
| 上板掉点 20+ | 校准集与真实分布不符 | 用板端实拍图做校准集 |
| 导出后 shape 不是 `1,6,3024` | `imgsz` 写错或加了 `nms=True` | 重新导出，`dynamic=False` |
| `3024` 变成 `3549` | `imgsz=416` 忘了改 | `416/8=52²=2704...`；一律先算公式 |
| 类别全错但指标正常 | `data.yaml` 里 `names` 顺序与标注不一致 | 固定顺序并写进 README |
| 红蓝互认 | `fliplr` 开了 | 关闭左右翻转 |
| 小目标全丢 | `imgsz` 太大导致小目标下采样过狠 / 数据里远景样本太少 | 加远景样本，或提高输入分辨率 |

---

# 第二部分 06-TPU(NPU)编程

## 2.1 运行时组件

```
CVI_NN_Init()                         全局初始化
  └─ CVI_NN_RegisterModel(path, &m)   加载 cvimodel（只做一次！）
       ├─ CVI_NN_GetInputTensor (m, 0)
       ├─ CVI_NN_GetOutputTensor(m, 0)
       ├─ CVI_NN_Forward(m, ins, 1, outs, n)   同步推理
       └─ CVI_NN_ReleaseModel(m)
```

链接（Makefile）：

```makefile
EXTRA_CFLAGS  += -I$(TPU_PATH)/include
EXTRA_LDFLAGS += -L$(TPU_PATH)/lib \
                 -Wl,--start-group \
                 -lcviruntime-static -lcvimath-static -lcvikernel-static \
                 -Wl,--end-group \
                 -lcnpy -lglog -lz
```

> 05 文档已提醒：`IVE_SUPPORT=1` 分支里的 `cv1835` 路径不能直接用，必须换成当前 SDK 的实际目录。

## 2.2 张量自检（**先跑这个，再谈推理**）

板端首次加载时把每个张量的元信息全打出来，比盲猜快 10 倍：

```cpp
static void dump_tensor(const char *tag, CVI_TENSOR *t, int idx)
{
    uint32_t ndim = 0;
    int32_t *shape = CVI_NN_GetTensorShape(t, &ndim);
    printf("========== %s %d ==========\n", tag, idx);
    printf("Name       : %s\n", CVI_NN_GetTensorName(t));
    printf("Shape      : ");
    for (uint32_t i = 0; i < ndim; ++i) printf("%d ", shape[i]);
    printf("\nFormat     : %d\n", CVI_NN_GetTensorFmt(t));
    printf("Count      : %d\n", CVI_NN_GetTensorCount(t));
    printf("Memory size: %d bytes\n", CVI_NN_GetTensorSize(t));
    printf("Type       : %d\n", CVI_NN_GetTensorType(t));
}
```

**判读规则（极其重要）**：

| 观察 | 结论 |
|------|------|
| `Memory size / Count == 4` | **float32 张量**，直接读写 `float*`，**不做反量化** |
| `Memory size / Count == 1` | int8 张量，需要 `QScale` / `Zero point` 反量化 |
| `Memory size / Count == 2` | fp16/bf16，需要转换 |
| 输出名带 `_f32` | 该输出**跳过了量化**（常见于检测头 Concat） |

## 2.3 输入填充（本项目：f32，NCHW，0..1，RGB）

VPSS 给的是三平面 `RGB_888_PLANAR`，stride 可能 > 宽，必须**逐行**拷：

```cpp
static void fill_input_f32(CVI_TENSOR *in, const VIDEO_FRAME_INFO_S &f)
{
    const VIDEO_FRAME_S &vf = f.stVFrame;
    float *dst = (float *)CVI_NN_GetTensorVirAddr(in);          // NCHW
    const size_t plane = (size_t)NPU_W * NPU_H;                  // 384*384

    const CVI_U8 *p[3] = {
        (const CVI_U8 *)vf.pu8VirAddr[0],
        (const CVI_U8 *)vf.pu8VirAddr[1],
        (const CVI_U8 *)vf.pu8VirAddr[2],
    };

    for (int c = 0; c < 3; ++c) {
        float *d = dst + c * plane;
        for (CVI_U32 y = 0; y < NPU_H; ++y) {
            const CVI_U8 *s = p[c] + y * vf.u32Stride[c];
            float *dl = d + y * NPU_W;
            for (CVI_U32 x = 0; x < NPU_W; ++x)
                dl[x] = s[x] * (1.0f / 255.0f);                  // 归一化关键
        }
    }
}
```

> **三种最常见的写法错误**：
> 1. `memcpy` 直接整块拷 → stride≠width 时图像错位（表现为整幅图斜切）；
> 2. 拷 uint8 到 `CVI_NN_GetTensorVirAddr` → 模型收到 `0..255` 的 float，输入饱和 → **全 0 检测**；
> 3. 忘掉 `/255` → 同上。

**通道顺序**：VPSS 是 `RGB_888_PLANAR`，模型用 `--pixel_format rgb` 转的，**顺序一致，不要交换**。
若发现框全错位/全无，先把 B、R 两平面互换验证一次（这是最快的二分法）。

## 2.4 前向与输出解析

```cpp
CVI_TENSOR *in  = CVI_NN_GetInputTensor(model, 0);
CVI_TENSOR *out = CVI_NN_GetOutputTensor(model, 0);

fill_input_f32(in, frame);

CVI_TENSOR *ins[1]  = { in };
CVI_TENSOR *outs[1] = { out };
CVI_NN_Forward(model, ins, 1, outs, 1);          // 同步阻塞
```

**输出布局**：`[1, 6, 3024, 1]`（板端打印）即 `[1, C, N]`，通道优先：

```
index(c, i) = c * N + i        // N = 3024
cx = p[0*N + i]   cy = p[1*N + i]   w = p[2*N + i]   h = p[3*N + i]
s0 = p[4*N + i]   s1 = p[5*N + i]
```

```cpp
typedef struct { float x1, y1, x2, y2, score; int cls; } Det;

static int decode(const float *p, int nc, int N, float conf_th, float iou_th,
                  float ratio, float padw, float padh,
                  int img_w, int img_h, Det *out, int max_out)
{
    int n = 0;
    float bx[1024], by[1024], bw[1024], bh[1024], bs[1024];
    int   bc[1024];

    for (int i = 0; i < N && n < 1024; ++i) {
        int best = 0; float bv = p[(4 + 0) * N + i];
        for (int c = 1; c < nc; ++c) {
            float v = p[(4 + c) * N + i];
            if (v > bv) { bv = v; best = c; }
        }
        /* 输出已是 0..1（logits 时加 sigmoid：1/(1+exp(-v))） */
        if (bv < conf_th) continue;

        float cx = p[0 * N + i], cy = p[1 * N + i];
        float w  = p[2 * N + i], h  = p[3 * N + i];
        float x1 = (cx - 0.5f * w - padw) / ratio;
        float y1 = (cy - 0.5f * h - padh) / ratio;
        float x2 = (cx + 0.5f * w - padw) / ratio;
        float y2 = (cy + 0.5f * h - padh) / ratio;
        bx[n] = fmaxf(0.f, x1); by[n] = fmaxf(0.f, y1);
        bw[n] = fminf((float)img_w, x2); bh[n] = fminf((float)img_h, y2);
        bs[n] = bv; bc[n] = best; ++n;
    }
    /* 逐类 NMS 略，见 PC 端 check_model.py 的 nms_per_class */
    for (int i = 0; i < n && i < max_out; ++i)
        out[i] = (Det){ bx[i], by[i], bw[i], bh[i], bs[i], bc[i] };
    return n;
}
```

**letterbox 逆变换**（与训练/PC 端严格一致）：

```cpp
float r = fminf((float)NPU_W / img_w, (float)NPU_H / img_h);
float padw = (NPU_W - img_w * r) * 0.5f;
float padh = (NPU_H - img_h * r) * 0.5f;
```

> 板上 VPSS 直接输出 384×384 已经**拉伸**过，没有 letterbox 过程。所以板上要么
> ① 把 `ratio/pad` 按"拉伸"处理（`ratio=NPU_W/img_w`，`pad=0`），
> ② 要么在 VPSS 之后自己做 letterbox（多一次拷贝，慢）。
> **两者必须与训练时一致**：训练/导出用 letterbox → 板上就得 letterbox；训练直接 resize → 板上按拉伸还原。混用会导致框位置系统性偏移（但**不会** 0 检测）。

## 2.5 生命周期与资源释放

```text
初始化 VB/SYS/VI/VPSS
→ RegisterModel（一次）
→ 查张量
→ 主循环：GetFrame → fill → Forward → decode → ReleaseFrame
→ ReleaseModel
→ 逆序释放 VPSS/VI/SYS/VB
```

**铁律**：模型只加载一次；每帧结束后必须 `CVI_VPSS_ReleaseChnFrame`；中途异常退出不释放会**残留 VB 池，只能重启板子**。

## 2.6 PC 端联调脚本（强烈建议保留）

转换前先确认 ONNX 在 PC 上的结果，作为板端"标准答案"：

```bash
cd ~/Desktop/study/RMDrone/sources/armor_deploy
python3 -m venv .venv && . .venv/bin/activate
pip install onnx onnxruntime numpy opencv-python-headless

python check_model.py --info-only --model best.onnx        # 核对 384/6/3024
python check_model.py --ppm test.ppm --conf 0.01            # 低阈值先看有没有框
python check_model.py --ppm test.ppm --conf 0.25 --names armor,car --draw --draw-input
```

产物 `out/<stem>_side.png` 左边原图、右边模型实际输入，**一眼看出预处理对不对**。

---

# 第三部分 实际情况（板端实测日志解读）

## 3.1 实测日志（原样）

```text
================================
       YOLO TPU INITIALIZE
================================
version: 1.4.0
armor Build at 2026-11-24 21:29:15 For platform cv181x
Max SharedMem size:884736
[OK] Model registered!
[OK] Get tensors successfully!
Input number : 1
Output number: 1

========== INPUT 0 ==========
Name       : images
Shape      : 1 3 384 384
Format     : 0
Count      : 442368
Memory size: 1769472 bytes
QScale     : 1.000000
Zero point : 0
Pixel fmt  : 100
Mean       : 0.000000 0.000000 0.000000
Scale      : 0.000000 0.000000 0.000000

========== OUTPUT 0 ==========
Name       : output0_Concat_f32
Shape      : 1 6 3024 1
Format     : 0
Count      : 18144
Memory size: 72576 bytes
QScale     : 1.000000
Zero point : 0
...
[OK] YOLO MODEL INITIALIZED!

================================
       RMDRONE RUNNING
================================
[RUN] Ctrl+C to exit
0 R:1298 B:3773 CT:2688
1 R:1629 B:2599 CT:3890
2 R:2430 B:1675 CT:7300
Golden 1629 1024 2599
wdrLEOnly:1
[NPU     0] 384x384 stride=384/384/384
[NPU] dumped /mnt/data/npu_input.ppm (384x384)
[NPU     0] Detection count: 0
[CV     0] saved /mnt/data/cv_00000.ppm (write 39.2 ms, 675 KB)

[NPU    30] 384x384 stride=384/384/384
[NPU    30] Detection count: 0
[CV    30] 1280x720 st=3840 | 间隔 151.15 ms | 均值 155.02 ms (6.5 fps)
[CV    30] saved /mnt/data/cv_00030.ppm (write 44.0 ms, 675 KB)
...
[CV   420] 1280x720 st=3840 | 间隔 161.52 ms | 均值 173.85 ms (5.8 fps)
^C[NPU   450] Detection count: 0
[RUN] stop, npu=451 cv=451
ISP Vipipe(0) Free pa(0x8b6c0000) va(0x0x3fd5055000)
[OK] YOLO MODEL RELEASED
==== rmdrone end ====
```

## 3.2 日志逐段解读

| 行 | 含义 | 判读 |
|----|------|------|
| `version: 1.4.0` | cvi runtime 版本 | 与转换时 SDK 版本一致才安全 |
| `For platform cv181x` | **模型是按 cv181x 构建的** | 能在本板加载 → 本链路就该用 cv181x（见 1.5 的说明） |
| `Max SharedMem size:884736` | 模型内部共享内存上限 **864 KB** | 换更大输入/更大模型时先看这个够不够 |
| `[OK] Model registered!` | `CVI_NN_RegisterModel` 成功 | 模型文件与 runtime 版本匹配 |
| `Input 0 / images / 1 3 384 384` | NCHW 384 | 与 main.cpp 里 `NPU_W/H=384` 对应（**旧代码是 360，已改**） |
| `Count 442368` | `384×384×3` | 元素数 |
| **`Memory size 1769472`** | `442368 × 4` | **float32 输入**，不是 int8 |
| `QScale 1.0 / Zero point 0` | 无量化 | 印证 fp32 |
| `Pixel fmt 100` | FP32 像素格式 | 与上一致 |
| `Output 0 / output0_Concat_f32 / 1 6 3024 1` | `[1, C, N]`，4 维（末尾有 1） | 解码时按 `ndim==4` 取 `dims[1]=6, dims[2]=3024` |
| **`Memory size 72576`** | `18144 × 4` | **float32 输出**，名字里的 `_f32` 也印证 |
| `[NPU 0] Detection count: 0` | 后处理筛出 0 个框 | **核心问题，见 3.4** |
| `[CV 30] 间隔 151.15 ms ... 6.5 fps` | 主循环节拍 | 见 3.5 |
| `[CV 0] saved ... (write 39.2 ms, 675 KB)` | 存 PPM | 675 KB = 640×360×3 |
| `R:1298 B:3773 CT:2688` / `Golden 1629 1024 2599` | ISP AWB 增益/色温收敛 | 正常，说明 AE/AWB 已锁 |
| `wdrLEOnly:1` | WDR 长曝光模式 | 正常 |
| `[RUN] stop, npu=451 cv=451` | 451 帧、两路同步 | 两路计数相等 = 没有丢帧逻辑错误 |
| `ISP Vipipe(0) Free ...` / `MODEL RELEASED` | 正常释放 | 无残留 |

## 3.3 三条硬结论

1. **当前板上跑的是 FP32 模型**：输入 4 B/元素、输出 4 B/元素，输出名带 `_f32`。
2. **模型本身加载/张量获取/前向调用都正常**（无 `CVI_NN` 报错），问题**全在后处理或输入填充**。
3. **6.4 fps 的主因是 fp32 推理 + 每帧存 PPM**，不是摄像头（VPSS 出 1280×720 才 ~150 ms 里占很小一部分）。

## 3.4 `Detection count: 0` 排查树（按概率排序）

```
Detection count == 0
├─ ① 输入被写成 uint8 / 没归一化  → 模型看到 0..255，输出全乱          【最可能】
│    验证：dump npu_input.ppm，用 PC 端 check_model.py 喂同一张图，
│          若 PC 有框而板端没有 → 确定是填充问题
├─ ② 输出被当 int8 反量化（(q-zp)*scale）→ 全是 0 或极小值
│    验证：打印输出 max/min。fp32 输出的 min/max 应落在 0..1；
│          若 min=max=0 或出现巨大负数 → ②
│    修复：直接 (float*)CVI_NN_GetTensorVirAddr(out) 读，不反量化
├─ ③ shape 4 维没处理 → N 取成 dims[2]=3024 之外的错值
│    验证：printf ndim 与每个 dim；[1,6,3024,1] 要取 dims[1]=6, dims[2]=3024
├─ ④ RGB/BGR 顺序反 → 分数极低（常 < 0.05），阈值 0.25 一过滤就 0
│    验证：把 B/R 平面互换再跑；或先把 conf 降到 0.01 看是否冒出框
├─ ⑤ 置信度阈值过高（0.25/0.5）+ 模型本身分数偏低
│    验证：打印"通过 conf 之前的最高分"
├─ ⑥ letterbox 与拉伸混用 → 框位置偏，但通常仍有框（可排除 0 检测）
└─ ⑦ 模型自己不行 → 训练 mAP 低 / 校准集错
     验证：PC 端 check_model.py 同一张图也无框
```

**最省时的二分法（建议立刻做）**：

```bash
# 1) 板上 dump 的 384×384 输入，拷回 PC
scp root@192.168.42.1:/mnt/data/npu_input.ppm .
# 2) PC 上让它走和板端"相同"的预处理，看有没有框
python check_model.py --ppm npu_input.ppm --size 384 --nc 2 --conf 0.01 \
       --noresize --draw
```

- **PC 有框、板端 0** → 板端填充/解码有 bug（① ② ③ ④）；
- **PC 也无框** → 模型或这张图本身的问题（⑦），别再改 C++。

**加一行"最高分"打印，比什么都快**：

```cpp
float gmax = 0.f;
for (int i = 0; i < N; ++i)
    for (int c = 0; c < nc; ++c)
        gmax = fmaxf(gmax, p[(4 + c) * N + i]);
printf("[NPU] top score = %.4f  (conf_th=%.2f)\n", gmax, conf_th);
```

- `top score ≈ 0.0000` → 输入没喂对（① 或 ②）；
- `top score = 0.3+` 而 count 仍 0 → 解码/NMS 逻辑错（③ 或 ⑤）。

## 3.5 实测量化：6.4 fps 从哪来

| 项 | 数值 | 说明 |
|----|------|------|
| 帧间隔 | 151~162 ms（均值 ~155 ms） | 11 次打印的稳定值 |
| 帧率 | **6.4~6.5 fps**（偶发降到 5.8 fps） | 420 帧处出现 161 ms 抖动 |
| 其中：PPM 写盘 | **39~44 ms / 帧** | 675 KB @ ~17 MB/s，SD 卡瓶颈 |
| 其中：NPU 推理（推定） | ~80~110 ms | 未见单独打点，需实测确认 |
| 其中：三平面→f32 拷贝 | 442368 次乘加，~2~5 ms | 384² 量级 |
| 总帧数 | 451 帧 / 约 70 s | 与 `451 × 0.155 s ≈ 70 s` 吻合 |

**结论**：

- **关掉调试存图**（`DBG_SAVE_JPG 0`、`DBG_DUMP_NPU_PPM 0`）理论可省 ~40 ms → 约 **8.5 fps**；
- **换成 INT8 cvimodel** 后输入/输出内存降到 442368 B / 18144 B，推理是最大收益项（fp32→int8 常见 2~3 倍）；
- 若仍不够，**降输入到 320×320**（`N = 2100`），计算量再降 ~30%；
- **不要**指望 VPSS 的 1280×720 是瓶颈 —— 那一路只在存图时才真正花时间。

**建议加的阶段计时**：

```cpp
auto t0 = std::chrono::steady_clock::now();
CVI_NN_Forward(model, ins, 1, outs, 1);
auto t1 = std::chrono::steady_clock::now();
int us = std::chrono::duration_cast<std::chrono::microseconds>(t1 - t0).count();
if (idx % 30 == 0) printf("[NPU] forward %d us\n", us);
```

## 3.6 本次实跑 vs 目标

| 指标 | 实测 | 目标 | 差距 | 手段 |
|------|------|------|------|------|
| 帧率 | 6.4 fps | ≥ 15 fps | −8.6 | 关调试 + INT8 + 320 输入 |
| 检测数 | 0 | 命中装甲板 | —— | 修输入/输出处理（3.4） |
| 模型格式 | FP32 | INT8 | —— | `model_deploy.py --quantize INT8` |
| 输入尺寸 | 384 | 384（或 320） | —— | 与 VPSS 同步改 |
| 共享内存占用 | 884736 B（上限） | 留余量 | —— | 换模型时关注 |

## 3.7 下一步待办（按顺序）

- [ ] 打开 `[NPU] top score` 打印，确认是 0.0000 还是正常分数 → 定位 3.4 的 ①/② 还是 ③/⑤
- [ ] 把 `npu_input.ppm` 拷回 PC 用 `check_model.py` 对比，确认 PC 侧有框
- [ ] 修正 input 填充：三平面逐行 → `float`，`/255`
- [ ] 修正输出解析：fp32 直读，`dims[1]=6, dims[2]=3024`，`index = c*3024 + i`
- [ ] 补 NMS（逐类，IoU 0.45）
- [ ] 关 `DBG_*`，加 forward 计时，重测帧率
- [ ] 用 INT8 重转模型，比对量化掉点（与 PC 端基线比）
- [ ] 若 384 仍慢 → 试 320×320，同步改 `NPU_W/H`、VPSS CHN0、VB 池
- [ ] 把最终参数（输入尺寸、阈值、类别顺序）写进 `doc/README` 与本文件

---

## 附录 A：命令速查

```bash
# ── 训练 ──
yolo detect train model=yolo11n.pt data=data.yaml imgsz=384 epochs=150 batch=32
yolo export model=best.pt format=onnx imgsz=384 opset=12 simplify=True

# ── 转换（Docker）──
docker load -i tpuc_dev_v3.4.tar.gz
docker run -it --name tpu-mlir -v "$PWD":/workspace -w /workspace sophgo/tpuc_dev:v3.4
model_transform.py --model_name armor --model_def best.onnx \
  --input_shapes [[1,3,384,384]] --mean 0,0,0 --scale 0.0039216 \
  --pixel_format rgb --keep_aspect_ratio --mlir armor.mlir
run_calibration.py armor.mlir --dataset calib_list.txt --input_num 200 -o armor_calib_table
model_deploy.py --mlir armor.mlir --quantize INT8 \
  --calibration_table armor_calib_table --chip cv181x --model armor_int8.cvimodel

# ── 板端 ──
scp armor_int8.cvimodel root@192.168.42.1:/mnt/data/
ssh root@192.168.42.1 '/root/rmdrone'

# ── PC 端对照 ──
python check_model.py --info-only --model best.onnx
python check_model.py --ppm npu_input.ppm --size 384 --nc 2 --conf 0.01 --draw
```

## 附录 B：本文件涉及的关键数字

```
输入 384×384×3  = 442,368 元素      ×4B = 1,769,472 B   (fp32)
输出 6×3024     =  18,144 元素      ×4B =    72,576 B   (fp32)
3024 = 48² + 24² + 12²  (384/8, /16, /32 三个尺度)
帧间隔 ~155 ms → 6.45 fps
PPM 675 KB = 640×360×3（降采样后的调试图）
451 帧 ≈ 70 s
```

---

## 修订记录

| 日期 | 内容 |
|------|------|
| 2026-11-25 | 初版：合并"训练集训练"与"板端编程"，加入 2026-11-24 21:29 实跑日志（FP32 模型、0 检测、6.4 fps）的解读与排查树 |
