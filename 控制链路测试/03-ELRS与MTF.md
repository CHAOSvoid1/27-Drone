---
tags: [无人机, 台架测试, 阶段03]
status: 基础通信已通过，安全与融合待验证
firmware: PX4 1.16.2
---

# 03 ELRS与MTF-02P测试流程

前置：[[02-PX4与地面站]]。下一步：[[04-Lichee与MAVLink]]。

## 固定端口分配

| 设备 | 飞控端口 | 协议与速率 |
|---|---|---|
| ELRS | UART6 / RC | CRSF |
| MTF-02P | UART4 / TELEM2 | MAVLink PX4，115200 |
| LicheeRV Nano | UART1 / TELEM1 | MAVLink Onboard，115200；当前线上帧为MAVLink 1 |

## A. ELRS检查

1. 全部拆桨，解锁开关保持停机位置。
2. 接收机TX接RX6、RX接TX6，按模块要求接5V和GND。
3. 在MicoConfigurator设置：接收机协议`CRSF`，端口`Radio Controller (UART6)`。
4. 检查CH1至CH16是否连续变化，校准Roll、Pitch、Throttle、Yaw的中点、端点和方向。
5. 核对通道5解锁开关、通道8紧急停机开关的安全位置。
6. 关闭遥控器做失联测试，确认飞控报告RC丢失；重新打开后应恢复，且不能自动解锁。

可用PX4控制台复核：

```sh
rc_input status
listener input_rc 5
```

## B. MTF-02P检查

1. MTF固定朝下，接UART4/TELEM2，5V供电并共地。
2. 在MicoConfigurator确认光流和测距均被识别。
3. 机架完成前只做链路门禁：模块置于有纹理、光照稳定的平面上方，确认测距与光流持续发布且EKF实际融合。
4. 多点精度、方向、安装偏移和长时间稳定性留到模块刚性安装在机架/测试架后执行。

PX4控制台：

```sh
listener sensor_optical_flow 10
listener distance_sensor 10
```

## 通过标准

- [x] ELRS使用UART6/CRSF，16通道可见。
- [ ] RC端点、方向、解锁与停机开关已复核。
- [x] 关闭遥控器后CRSF进入搜索、`input_rc`停止刷新且`manual_control_signal_lost=True`。
- [x] 遥控器重新开机后CRSF重新锁定、`input_rc`恢复刷新且失联标志清除。
- [x] MTF光流和独立测距主题已发布。
- [x] 约0.79 m静态测距、光流质量和零速噪声合理。
- [x] 主EKF实例确认`cs_opt_flow=True`、`cs_rng_hgt=True`，两个援助源均`fused=True`且未拒绝。
- [>] 多点测距精度、运动方向和连续性移至机架完成后的标定阶段。
- [ ] 无磁力计、`cs_yaw_align=False`及`heading_good_for_control=False`对控制门禁的影响完成评估。
