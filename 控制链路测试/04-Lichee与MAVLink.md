---
tags: [无人机, 台架测试, 阶段04, MAVLink]
status: 双向MAVLink、零/非零轨迹目标及setpoint丢失检测均已通过
firmware: PX4 1.16.2
---

# 04 LicheeRV Nano与PX4 MAVLink测试流程

前置：[[03-ELRS与MTF]]。下一步：[[05-GC4653视觉链路]]。

## 1. 已确认接线

Nano PA19是`UART1 TX`，PA18是`UART1 RX`。UART必须交叉连接：

```text
飞控 TX1  ─────→ Nano PA18 / UART1 RX
飞控 RX1  ←───── Nano PA19 / UART1 TX
飞控 GND  ────── Nano GND
```

当前实物已确认按上述交叉方式焊接，无需交换PA18和PA19。

两块板都由同一个USB拓展坞供电时，USB电源地通常已经通过拓展坞和电脑相连，但UART仍要增加上图的GND直连。不要依赖电脑内部的长地回路传递串口参考电位。Nano继续独立Type-C供电，不接飞控TELEM1的5V。

## 2. 上电前检查

1. 两端断电，保持全部桨叶拆除。
2. 用通断档确认TX、RX无短路，信号线没有对5V或GND短路。
3. 确认飞控TX1到PA18、飞控RX1到PA19。
4. 先给飞控和Nano分别上电，确认无异常发热，再连接USB调试。

## 3. PX4配置TELEM1

MTF已占UART4/TELEM2，ELRS已占UART6/RC，因此Nano使用UART1/TELEM1。

在MicoConfigurator的“端口设置”中，把TELEM1设为MAVLink伴随计算机端口；如果界面无法完整设置，则在QGroundControl参数页设置：

```text
MAV_0_CONFIG   = TELEM1
MAV_0_MODE     = Onboard
MAV_0_FORWARD  = Disabled
MAV_0_FLOW_CTRL= Off
SER_TEL1_BAUD  = 115200
```

首次点对点联调关闭`MAV_0_FORWARD`，避免端口转发或回显形成消息环路。如果TELEM1已被其他`MAV_x_CONFIG`实例占用，先把旧实例改为Disabled并重启，再分配给`MAV_0_CONFIG`。保存后重启飞控。

在QGC MAVLink Console检查：

```sh
param show MAV_0_CONFIG
param show MAV_0_MODE
param show MAV_0_FORWARD
param show MAV_0_FLOW_CTRL
param show SER_TEL1_BAUD
mavlink status
```

通过条件：`mavlink status`中存在绑定TELEM1的实例，模式为Onboard，速率为115200，且没有端口冲突。当前实测TELEM1为`/dev/ttyS0`，线上使用MAVLink 1帧；现有测试脚本按MAVLink 1收发。

## 4. Nano配置UART1

通过USB NCM登录：

```powershell
ssh root@10.128.80.1
```

在Nano执行：

```sh
devmem 0x03001068 32 0x6   # PA18 = UART1 RX
devmem 0x03001064 32 0x6   # PA19 = UART1 TX
ls -l /dev/ttyS1
stty -F /dev/ttyS1 115200 raw -echo -ixon -ixoff
stty -F /dev/ttyS1 -a
```

Sipeed官方给出的UART1设备是`/dev/ttyS1`。以上pinmux设置每次重启后可能失效，首次联调先手动执行；验证通过后再写入启动脚本。

## 5. 第一次只收不发测试

保持飞控未解锁，在Nano执行：

```sh
timeout 5 dd if=/dev/ttyS1 bs=1 count=256 2>/dev/null | hexdump -C
```

预期：5秒内出现持续变化的二进制数据。MAVLink 2帧通常以`fd`开头，但单独看到`fd`不能证明CRC正确。

若没有数据，按顺序检查：

1. 飞控TX1是否接Nano PA18。
2. 是否焊接了飞控GND到Nano GND。
3. `MAV_0_CONFIG`是否为TELEM1并已重启。
4. 两端是否都是115200、无流控。
5. PA18 pinmux是否在本次Nano启动后执行。

## 6. MAVLink解析与双向测试

原始字节通过后，再安装或上传轻量MAVLink测试程序。程序至少完成：

1. 解析`HEARTBEAT`并校验CRC。
2. 显示飞控system ID、component ID、消息序号和接收频率。
3. 读取`ATTITUDE`、`BATTERY_STATUS`和`LOCAL_POSITION_NED`。
4. 由Nano以独立component ID发送1 Hz `HEARTBEAT`。
5. 连续运行10分钟，记录CRC错误、序号跳变和断链次数。

同时在PX4执行：

```sh
mavlink status
```

确认接收字节数持续增加。拔开UART信号或停止Nano程序，程序应报告断线；恢复后应自动重新收到心跳，飞控始终保持未解锁。

## 7. 后续控制门禁

双向心跳、零速度目标和未解锁Offboard均已通过。当前只验证setpoint中断保护：保持Nano心跳继续、停止零速度setpoint，确认飞控按`COM_OF_LOSS_T`退出Offboard。通过后仍需完成RC失联与电源门禁，再进入[[06-无桨电机测试]]。

## 通过标准

- [x] 信号线已确认TX1→PA18、RX1←PA19。
- [x] 飞控GND与Nano GND已直接连接。
- [x] PX4 TELEM1为Onboard、115200、无流控且转发关闭；当前线上帧为MAVLink 1。
- [x] Nano `/dev/ttyS1`收发计数持续增长，双向UART通过。
- [x] PX4已解析Nano测试HEARTBEAT：sysid 42、compid 191、1 Hz、CRC通过。
- [x] 双向心跳连续10分钟：PX4 lost 0，Nano CRC错误0、序号缺口0。
- [x] 脚本停止后链路停止；不重启PX4重新运行可恢复，飞控保持未解锁。
- [x] 未解锁Offboard中收到实时`trajectory_setpoint.velocity=[0,0,0]`。
- [x] 未解锁Offboard中收到与Nano一致的非零`trajectory_setpoint.velocity=[0.20,-0.10,0.00]`。
- [x] setpoint停止后`failsafe_flags.offboard_control_signal_lost=True`，旧控制消息时间戳不再刷新。
- [ ] 实际Offboard失联切模动作在SITL或完整无桨安全门禁满足后验证。

## 资料

- [Sipeed UART1引脚与pinmux](https://wiki.sipeed.com/hardware/zh/lichee/RV_Nano/5_peripheral.html)
- [PX4串口配置](https://docs.px4.io/main/zh/peripherals/serial_configuration)
- [PX4 MAVLink外设配置](https://docs.px4.io/main/en/peripherals/mavlink_peripherals)





## 联调脚本

使用[[脚本/实机链路/mavlink_link_test.py]]进行60秒持续心跳、常用消息解析、CRC和序号统计。

脚本已扩展`--velocity-seconds --vx --vy --vz`，用于机架完成前的未解锁非零速度目标验证。三轴均硬限制在±0.5 m/s；脚本本身不会切换模式或解锁。正式测试时由操作者在setpoint流有效期间临时进入Disarmed Offboard，读取`trajectory_setpoint`后立即回到Stabilized。

60秒基础联调、600秒干净统计和断线恢复均已通过，证据见[[测试记录/2026-09-23至29-系统联调总记录]]。

Nano使用Buildroot `/etc/init.d/rcS`按名称执行`S??* start`。使用[[脚本/实机链路/S98uav-uart]]在启动末段自动配置PA18/PA19 pinmux和`/dev/ttyS1` 115200；该脚本只配置UART，不自动启动MAVLink应用。

重启实测确认`S98uav-uart`自动执行成功，重启后的20秒链路复测CRC错误0、序号缺口0。`MAV_CMD_REQUEST_MESSAGE`已获得`AUTOPILOT_VERSION`及`COMMAND_ACK result=0`。未解锁零速度Offboard轨迹也已验证。





## Offboard存活测试说明

Stabilized模式下只发布实时`offboard_control_mode`，外部`trajectory_setpoint`需在飞控接受Offboard setpoint时才发布。首次测试已确认`velocity=True`，且飞控全程Disarmed、Stabilized、无failsafe。证据见[[测试记录/2026-09-23至29-系统联调总记录]]。


未解锁Offboard实测中，零速度目标被PX4转换为实时`trajectory_setpoint.velocity=[0,0,0]`；进入和退出前后均为Disarmed且无failsafe。

原失联参数为`COM_OF_LOSS_T=1.0 s`、`COM_OBL_RC_ACT=0 (Position)`。由于台架水平速度估计不稳定，本次验证已将`COM_OBL_RC_ACT`临时设为`2 (Stabilized)`并保存。setpoint停止后实测`offboard_control_signal_lost=True`，旧控制消息时间戳超过352秒；随后手动退回Stabilized，保持Disarmed且无failsafe。实际失联切模动作留到SITL或完整无桨安全门禁满足后验证。


