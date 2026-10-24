# OPENCV稳定视频流获取

## [opencv-mobile](https://github.com/nihui/opencv-mobile)

问题：

虽然5.0.0更新了LicheeRV Nano (SG2002)新排线的算法，但是完全不能用，会强制到**V4L2**导致设备无法接收

```bash
opencv-mobile MIPI CSI camera with cvi
opencv-mobile HW JPG decoder with cvi
cannot find v4l device with VIDEO_CAPTURE and STREAMING
Error: Cannot open camera.
```

如果修改代码用RISV-C的库会报错**RVV HAL**无法使用，但是很多库默认开启 

> GCC 10.2.0 在编译 OpenCV 的 RVV 路径时存在已知的深层兼容性问题，即使关闭 Scalable 路径，也可能遇到其他 RVV 0.7.1 相关的编译错误。如果上述命令仍然报错，最务实的选择是**放弃在 OpenCV 5.0 上启用 RVV**，让整个构建完全脱离 RVV 路径。但这需要修改工具链文件或 OpenCV 的 CMake 逻辑，侵入性较强。

## [CVI_HW_OpenCV](https://github.com/ret7020/CVI_HW_OpenCV)

使用这个会报错

```bash
opencv-mobile MIPI CSI camera with cvi
Error loading shared library /mnt/system/lib/libsys.so: No such file or directory
Error loading shared library /mnt/system/lib/libsys.so: No such file or directory
Error loading shared library /mnt/system/lib/libsys.so: No such file or directory
Error loading shared library /mnt/system/lib/libae.so: No such file or directory
Error loading shared library /mnt/system/lib/libawb.so: No such file or directory
Error loading shared library /mnt/system/lib/libisp.so: No such file or directory
Error loading shared library /mnt/system/lib/libcvi_bin_isp.so: No such file or directory
Error loading shared library /mnt/system/lib/libsys.so: No such file or directory
opencv-mobile HW JPG decoder with cvi
Error loading shared library /mnt/system/lib/libsys.so: No such file or directory
Error loading shared library /mnt/system/lib/libsys.so: No such file or directory
cannot find v4l device with VIDEO_CAPTURE and STREAMING
Error: Cannot open camera.
```

指定了sys库后

```bash
# export LD_LIBRARY_PATH=/mnt/system/usr/lib:$LD_LIBRARY_PATH
# ./build/27RMDrone
opencv-mobile MIPI CSI camera with cvi
opencv-mobile HW JPG decoder with cvi
ISP Vipipe(0) Allocate pa(0x8ab80000) va(0x0x3ff57d0000) size(291424)
awbInit ver 6.9@2021500
0 R:1400 B:3100 CT:2850
1 R:1500 B:2500 CT:3900
2 R:2300 B:1600 CT:6500
Golden 1024 1024 1024
WB Quadratic:0
isWdr:0
19700101 00:14:35.311 4051 E isp setNoiseProfile:3460 Noise profile get fail. Please check
###########   PinMux  #######################################################################
###########   PinMux End  #######################################################################
ViPipe:0,===OS04A10 1520P 30fps 12bit LINE Init OK!===
binName = /mnt/cfg/param/cvi_sdr_bin
********************************************************************************
cvi_bin_isp message
gerritId:      97347          commitId:      fc6ce647       
md5:           d6db2297ddfd44e8252c1f3f888f47b2
sensorNum      1              
sensorName0    5440577        

PQBIN message
gerritId:      
              commitId:      fc6ce647       
md5:           d6db2297ddfd44e8252c1f3f888f47b2
sensorNum      1              
sensorName0    4653           

author:        lxowalle       desc:          gc4653 30fps   
createTime:    2024-03-26 13:41:34version:                      
tool Version:       v3.0.5.48           mode:      
********************************************************************************
sensorName(0) mismatch, mwSns:5440577 != pqBinSns:4653
VI_SDK_IOC_S_CTRL - vi_sdk_get_chn_frame NG, Operation not permitted
CVI_VI_GetChnFrame failed c00e8040
VI_SDK_IOC_S_CTRL - vi_sdk_get_chn_frame NG, Operation not permitted
CVI_VI_GetChnFrame failed c00e8040
VI_SDK_IOC_S_CTRL - vi_sdk_get_chn_frame NG, Operation not permitted
CVI_VI_GetChnFrame failed c00e8040
VI_SDK_IOC_S_CTRL - vi_sdk_get_chn_frame NG, Operation not permitted
CVI_VI_GetChnFrame failed c00e8040
VI_SDK_IOC_S_CTRL - vi_sdk_get_chn_frame NG, Operation not permitted
CVI_VI_GetChnFrame failed c00e8040
VI_SDK_IOC_S_CTRL - vi_sdk_get_chn_frame NG, Operation not permitted
CVI_VI_GetChnFrame failed c00e8040
Image saved as captured.jpg
ISP Vipipe(0) Free pa(0x8ab80000) va(0x0x3ff57d0000)
```

> **核心问题在于 ISP 初始化时加载了错误的传感器配置文件**



但是配置文件没有问题

```bash
# cat /mnt/data/sensor_cfg.ini
[source]

;type = SOURCE_USER_FE

dev_num = 1

; section for sensor

[sensor]

; sensor name
name = GCORE_GC4653_MIPI_4M_30FPS_10BIT

bus_id = 4

sns_i2c_addr = 29

mipi_dev = 0

lane_id = 4, 3, 2, -1, -1

pn_swap = 0, 0, 0, 0, 0

mclk_en = 1

mclk = 1

;port = 0

;pin = 2

;pol = 1

;fps = 30
```

查询了`cvi_sdr_bin`文件

`mwSns:5440577 != pqBinSns:4653` 说明，**驱动层实际加载的传感器（OS04A10，ID 5440577）与你配置的 GC4653 并不一致**。


