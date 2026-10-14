# 脚本分类与用途

## 实机链路

| 文件 | 用途 | 注意事项 |
|---|---|---|
| `实机链路/nano_motor_test.py` | Nano通过MAVLink 310测试单个真实电机 | 当前唯一执行器测试脚本；每次只允许M1～M4中的一个 |
| `实机链路/部署Nano电机测试.ps1` | 上传上述脚本并运行self-check和dry-run | 部署阶段不发送实时电机命令 |
| `实机链路/mavlink_link_test.py` | UART双向通信、心跳、消息统计和受限Offboard setpoint测试 | 能发送setpoint，按`04-Lichee与MAVLink.md`使用 |
| `实机链路/S98uav-uart` | Nano启动时恢复PA18/PA19 pinmux和`/dev/ttyS1` 115200 | 安装到Nano的`/etc/init.d`，本身不启动控制程序 |

## 通信诊断

| 文件 | 用途 |
|---|---|
| `通信诊断/mavlink_protocol_observer.py` | 观察PX4身份、心跳、版本和ACK；只发送伴随心跳及版本请求 |
| `通信诊断/部署非控制测试.ps1` | 上传观察器和环境检查脚本 |
| `通信诊断/运行MAVLink非控制观察.ps1` | 在Nano上启动只读为主的MAVLink观察 |
| `通信诊断/check_pymavlink_environment.sh` | 检查Nano架构、Python、依赖、存储和wheel兼容性 |
| `通信诊断/安装pymavlink离线包.ps1` | 从`测试备份/python-packages`离线安装pymavlink |
| `通信诊断/验证pymavlink安装.ps1`、`verify_pymavlink.py` | 验证安装和低内存可用的MAVLink dialect接口 |

## SITL仿真

| 文件 | 用途 | 边界 |
|---|---|---|
| `SITL仿真/nano_sitl_command_source.py` | 产生零速、限速运动、停止的JSON测试序列 | 不直接访问真实飞控串口 |
| `SITL仿真/px4_sitl_bridge.py` | 将JSON目标转为本机PX4 SITL的MAVLink setpoint | 只连接本机仿真端口，不解锁、不切换真实飞控模式 |

## 使用顺序

1. 实机电机链路：阅读`../07-端到端联调.md`。
2. UART或MAVLink异常：使用`通信诊断`目录。
3. 视觉算法接入前的闭环验证：使用`SITL仿真`目录并阅读`../08-AirSim硬件在环与定位方案.md`。
