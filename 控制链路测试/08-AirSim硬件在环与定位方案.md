---
tags: [无人机, AirSim, HITL, 惯性导航, 光流, UWB]
status: Nano至真实电机链路已通过；PX4 1.16.2 HITL固件已构建，待备份参数后刷写首测
date: 2026-10-03
---

# 08 AirSim硬件在环与定位方案

## 目标与边界

本阶段验证以下闭环：

```text
Unreal/AirSim 场景与动力学
→ 模拟 IMU、气压计、磁力计、相机、光流/测距或 UWB
→ 实机 MicoAir H743 V2（PX4 1.16.2）
→ 实机 LicheeRV Nano 上的任务状态机与 Offboard 指令
→ PX4 姿态/位置控制和控制分配
→ 模拟电机输出返回 AirSim
→ 虚拟无人机运动
```

真实四合一 ESC 和电机不接入该闭环。真实电机只按 [[06-无桨电机测试]] 和 [[07-端到端联调]] 做独立、无桨、人工逐路验证。HITL 期间必须确认 PX4 不向真实电机端口输出可驱动信号。

本阶段终点是从虚拟机库起飞、沿预定安全走廊进入观察区、稳定悬停，并能在定位源降级时停止推进或回到安全状态。目标识别仅用于记录和观察区触发，不在本阶段验证接触、撞击或追逐动作。

## 本机已有条件

| 项目 | 当前结果 |
|---|---|
| Unreal Engine | `H:\VisualDrone\Epic\unrealengine\UE_4.27` |
| AirSim | `H:\VisualDrone\AirSim\AirSim-1.8.1-windows` |
| 可用工程 | `Blocks`、`CityParkEnvironmentCollec`、`SuperGridStarterpack` |
| AirSim 插件 | 三个 `.uproject` 均已启用；Blocks 含完整插件源码/构建产物 |
| 用户配置 | `C:\Users\chaos\Documents\AirSim\settings.json` 为 PX4 SITL、TCP 4560、LockStep 配置 |
| PX4 SITL | WSL Ubuntu 22.04，账号`hw`；旧SITL目录与HITL 1.16.0修改目录均已识别 |
| 实机链路 | Nano PA18/PA19 ↔ PX4 TELEM1 已完成双向 MAVLink 与 Offboard setpoint 验证 |

当前 `settings.json` 是 **SITL 配置**：`UseSerial=false`、`UseTcp=true`、`LockStep=true`。不能直接用它连接实机飞控做 HITL。

### 2026-10-03 WSL环境补充

- WSL发行版：Ubuntu 22.04.5 LTS（Jammy）。
- 当前账号：`hw`。
- 已发现源码目录：`/home/hw/PX4-Autopilot`。
- 历史命令表明曾运行`make px4_sitl_default none_iris`，并存在`~/hitl/px4.1.16.0`和`px4v1.15.2`等旧目录。
- 本次Git信息没有取得，因为误把说明文字`cd <上一步找到的PX4-Autopilot路径>`当成命令执行；这不是仓库故障。

应重新执行：

```bash
cd /home/hw/PX4-Autopilot
git describe --always --dirty
git branch --show-current
git status --short

cd ~/hitl/px4.1.16.0 2>/dev/null && {
  git describe --always --dirty
  git branch --show-current
  git status --short
}
```

首次实机PX4 HITL不依赖WSL账号：AirSim运行在Windows，飞控经Windows串口接入。继续使用`hw`可以避免重复安装依赖和目录权限问题；新账号只影响源码、构建缓存和脚本路径，不影响飞控固件本身。

#### 源码审查结果

- `/home/hw/PX4-Autopilot`当前为`master`上的`7d133b82-dirty`，且缺少完整NuttX平台内容；只保留作旧SITL工作目录，不用于H743硬件固件构建。
- `~/hitl/px4.1.16.0`位于`v1.16.0`提交`6ea3539157`、分支`hw`，包含多处未提交修改和子模块状态变化，不是可直接覆盖的干净仓库。
- 其中与MicoAir HITL直接相关的有效改动是给`boards/micoair/h743-v2/default.px4board`加入`CONFIG_MODULES_SIMULATION_PWM_OUT_SIM=y`；其余已查看的Commander、Mixer、MAVLink、`HIL_ACTUATOR_CONTROLS`和`PWMSim`差异主要为调试日志。
- Commander原逻辑在HITL状态令`actuator_armed.lockdown=true`，用于阻止真实电机输出；旧源码注释明确保留了这项保护。
- 当前飞控实际运行厂商PX4 1.16.2，不能仅凭旧1.16.0源码判断其中已经编译`pwm_out_sim`。刷写前先在当前固件NSH执行只读模块检查；若模块存在，优先保留当前固件。

#### 2026-10-03 HITL固件构建结果

- 当前飞控已确认：`MICOAIR_H743_V2`、PX4 1.16.2、Git提交`54f0455ffcd755534539a7cf33a09a20bf71d29d`、NuttX提交`886acbbdb4f061e5c0ce1a76afbcfa7cb7df9849`。
- 当前厂商固件执行`pwm_out_sim status`返回`command not found`，不能形成AirSim HITL所需的模拟执行器回传闭环。
- 已在独立目录`/home/hw/hitl/px4.1.16.2-hitl`准备与机上固件提交完全一致的源码；没有覆盖`~/hitl/px4.1.16.0`或旧SITL目录。
- 对源码的功能修改只有`boards/micoair/h743-v2/default.px4board`增加`CONFIG_MODULES_SIMULATION_PWM_OUT_SIM=y`。
- `make micoair_h743-v2_default`构建成功；链接映射和NuttX内建命令表均包含`pwm_out_sim_main`/`pwm_out_sim`。
- 固件文件：`测试备份/micoair_h743-v2_default-px4-1.16.2-hitl.px4`，大小`1712056`字节，SHA-256为`b2c18206e3f2748a9a238d81cbce4ad01adcd92ec141fae673e6cd04564ceffb`。
- FLASH占用`1814088 / 1835008`字节（98.86%），仍在链接区容量内，但余量很小；后续不要在该版本随意增加日志或模块。
- 该固件尚未刷写。刷写前必须导出当前参数，并保留现有厂商1.16.2固件文件或可重新下载的版本。
- 刷写前参数已保存为`测试备份/MicoAir743v2_Copter_PX4v1.16.2-before-HITL.param`，共1054个不重复条目，SHA-256为`11ee29a6a8d968e453e4f48d485ad168169a1824057698b7e6414157a910513a`。
- 参数备份与HITL固件内置参数表逐项比对后，1054项中只有QGC导出校验元数据`_HASH_CHECK`不是PX4参数；其余1053项名称全部存在。整包导入后仍需重启并复核关键参数，且该生产备份中的`SYS_HITL=0`、`SYS_AUTOSTART=4001`会恢复真实飞行状态。

#### 刷写后启动验证

- `ver all`确认硬件、PX4提交、NuttX提交和GCC版本均与构建基线一致，构建时间更新为2026-10-03 18:43:49。
- `pwm_out_sim status`返回`not running`，证明模块已成功编入；普通4001机架下不启动符合预期。
- 参数区成功加载，`SYS_AUTOSTART=4001`仍保留；刷写没有自动切换HITL。
- 启动日志中的QMC5883L未发现、一次IMU重复时间戳及`param 65535 invalid`暂未造成启动失败。HITL会屏蔽板载传感器；恢复实机前仍需单独复核罗盘和IMU状态。
- 已生成`测试备份/AirSim-settings-HITL-COM16.json`。该文件只建立COM16串口HITL和QGC UDP转发，不让AirSim写入旧版参数名。
- AirSim运行日志已确认成功打开COM16、收到PX4心跳，并向PX4提供HIL数据；日志出现GPS锁定和Home位置建立。
- 独立监听UDP 14550共60秒收到24820包、1734797字节，MAVLink 1/2帧头均正常，证明AirSim的QGC代理能够持续转发。
- QGC最终通过UDP 14550成功连接。该AirSim版本要求接收端在代理建立时已经监听；稳定启动顺序为：QGC先监听UDP并关闭Pixhawk/USB自动连接，然后启动AirSim独占COM16。
- HITL运行检查通过：`SYS_AUTOSTART=1001`、`SYS_HITL=1`，`pwm_out_sim`已运行并使用`HIL_ACT`前缀，通道0～3分别映射电机101～104。
- `vehicle_imu`设备类型为`SIMULATION`且时间戳新鲜；`vehicle_attitude`持续发布；`vehicle_local_position`的XY、Z、水平和垂直速度均为有效，且不是dead reckoning。
- USB MAVLink链路在`/dev/ttyACM0 @2000000`持续双向传输，当前汇总丢包率0.3%。代理合并多个MAVLink系统后各sysid的序列丢失计数不宜直接当作物理链路丢包率。
- 当前仍为未解锁状态，四个HIL输出均为disarmed值900；`actuator_outputs_sim`闭环和虚拟解锁尚未验证。`heading_good_for_control=False`，在其恢复或确认原因前不进行虚拟解锁。
- 后续检查显示Commander处于Disarmed/Hold且未进入failsafe；姿态、局部位置、全局位置和Home均有效。无RC和无Offboard输入对应的`manual_control_signal_lost=True`、`offboard_control_signal_lost=True`符合当前测试条件。
- EKF状态标志已完成倾斜和航向对准，正在融合GNSS位置/速度、磁航向和气压高度；没有磁场故障、创新拒绝或惯性推算状态。此前航向不可控标志不再构成当前门禁。
- `actuator_outputs_sim`以新鲜时间戳发布16路零输出，符合未解锁状态，证明模拟执行器输出主题已建立。下一步先确认真实`pwm_out`和`dshot`驱动未运行，再执行虚拟解锁。

当前Windows串口候选：`COM16 = USB VID_1B8C/PID_0036`，推定为MicoAir/PX4；`COM14 = USB VID_359F/PID_2120`，已确认是LicheeRV Nano。正式写入HITL配置前仍应通过插拔飞控复核COM16。

AirSim 官方文档支持 PX4 HITL，但公开测试硬件列表没有 MicoAir H743 V2，且文档对应的 PX4 版本较旧。因此先做 SITL 回归，再单独验证该板和 PX4 1.16.2 的 HITL 兼容性；不能直接改生产参数开始飞行。

## 场地导入结论

可以导入比赛场地。目前 `H:\VisualDrone` 中没有发现比赛场地工程或模型，需取得以下任一种输入：

1. 官方 Unreal Engine 4.27 工程或关卡包；
2. FBX/OBJ/GLTF 等模型和贴图；
3. CAD 模型，经 DCC 工具转换并按米制比例导出；
4. 场地尺寸图，根据尺寸用 UE4 基础几何体重建。

导入后必须检查比例、坐标原点、碰撞体、地面材质、光照、机库出口净空和 Player Start 高度。先建立低细节碰撞版完成控制联调，再替换高细节视觉资产。场地坐标约定固定为 NED，并保存 `Unreal厘米坐标 ↔ 场地米坐标 ↔ PX4局部NED` 的转换记录。

## 总体分层

### 第 0 层：真实执行端分段证据

本层已通过，沿用既有证据：

- PX4 经 QGC 能逐路驱动四个无桨电机；
- Nano 指令能到达 PX4 内部控制目标；
- Nano经UART1/MAVLink发送`VEHICLE_CMD_ACTUATOR_TEST(310)`，PX4接受clear/start/stop，M1以8%短时转动并停止，M2～M4无误动作；见[[测试记录/2026-10-03-Nano至电机端到端通过]]。

这一层只证明物理链路存在，不验证飞行动力学闭环。

### 第 1 层：恢复 AirSim + PX4 SITL 基线

1. 复制并备份当前 `settings.json`，保留现有 TCP/LockStep 配置。
2. 在 WSL 中确认 PX4-Autopilot 路径、Git 版本、构建目标和上次启动命令。
3. 启动 AirSim Blocks，再启动 PX4 SITL；确认 TCP 4560 建链。
4. QGC 只连接 SITL，核对姿态、位置、解锁和模拟电机输出。
5. 运行`脚本/SITL仿真/nano_sitl_command_source.py`与`px4_sitl_bridge.py`，确认Nano的零速/小速度命令改变SITL目标，并在输入超时后归零。
6. 记录 PX4 ULog、AirSim 日志和 Nano 单调时间戳。

通过标准：连续 10 分钟无协议错误；命令延迟和丢包可统计；Nano 退出后 0.5 秒内目标归零；虚拟机体不继续接受陈旧目标。

### 第 2 层：建立比赛场地低细节版

1. 获取官方场地资产或尺寸图。
2. 以 `SuperGridStarterpack` 的副本作为快速灰盒工程；保留原工程不改。
3. 建立地面、机库、出口、观察区和障碍物的碰撞体。
4. 设置 AirSimGameMode、Player Start 和 PX4Multirotor Pawn。
5. 标定场地原点和航向，建立一组人工测量基准点。
6. 用 AirSim 真值核对任意两点的距离、方向和高度。

通过标准：关键尺寸误差不超过项目设定容差；碰撞体无漏碰；出生点无自由落体；相机在机库内确实看不到观察区目标。

### 第 3 层：SITL 中验证定位方案 A（惯性 + 光流 + 测距）

定位链设计：

```text
IMU：高频姿态与短时速度传播
向下光流：约束水平速度漂移
ToF/距离：约束离地高度和光流尺度
视觉地标：进入观察区后提供位置/航向修正
```

测试顺序：

1. 使用 AirSim 真值作为评分基准，但不直接给控制器使用。
2. 先验证 IMU、光流和距离消息的坐标方向、单位、时间戳与更新率。
3. 关闭 GPS，起飞后沿预定短走廊进入观察区并悬停。
4. 分别注入光流短时中断、低纹理地面、ToF 跳变和相机未检出。
5. 记录 PX4 `vehicle_local_position`、估计误差、创新量、复位计数和定位有效标志。
6. 当水平位置不可信、光流质量过低或距离无效时，状态机停止推进并进入悬停/降落等待。

纯 IMU 只能用于短时间传播，不能作为长时间位置保持来源。机库到观察区的距离、速度和允许漂移必须通过仿真统计后确定。

### 第 4 层：SITL 中验证定位方案 B（UWB + 惯性备份）

先定义与具体 UWB 型号无关的接口：时间戳、局部坐标、协方差、有效标志和质量值。官方模块发布后再增加协议适配层。

测试顺序：

1. AirSim 真值经过噪声、延迟和丢包模型生成模拟 UWB 位置。
2. 将 UWB 作为 PX4 外部位置观测，核对坐标系和时间同步。
3. 比较 UWB、光流/ToF 和 AirSim 真值，不让 Nano 同时向 PX4 注入相互冲突的位置源。
4. 注入固定偏置、随机跳变、延迟、遮挡和完全失联。
5. UWB 有效时按局部路径进入观察区；失效时回退到光流/ToF和短时惯性，超出时间或协方差门限则停止任务。

通过标准：UWB 不可用不会造成位置突跳后继续推进；恢复时不会立刻接受离群点；同一任务状态机可在 A/B 两种定位源之间切换。

### 第 5 层：实机飞控 HITL 可行性门禁

1. 导出当前 PX4 1.16.2 参数和接线基线。
2. 断开 4S 主电源，拆桨；确认 ESC 不具备驱动电机的供电条件。
3. 使用独立 HITL 参数集，启用 PX4 硬件仿真模式并选择兼容的 HIL 四旋翼机架。
4. AirSim 改为串口连接实机飞控；串口、波特率、系统 ID 与 QGC 转发只能有一套明确配置。
5. 先只确认 HIL_SENSOR/HIL_GPS 输入和 HIL_ACTUATOR_CONTROLS 返回，不运行 Nano 控制程序。
6. 确认实机 IMU/光流/ToF不会与模拟传感器同时进入 EKF。
7. 再连接 Nano 到 TELEM1，运行与 SITL 相同的有限状态机和小范围测试目标。
8. 验证遥控接管、Nano 心跳中断、串口断开、定位失效和 AirSim 暂停时均进入预定安全状态。

若 AirSim 1.8.1 与 MicoAir PX4 1.16.2 的 HIL 消息或启动流程不兼容，停止在本层，改用 PX4 当前支持的 Gazebo HITL 验证飞控硬件；AirSim继续用于场景、相机和算法的 SITL/联合仿真。

### 实机飞控HITL首次启动顺序

首跑只验证AirSim与实机PX4之间的模拟传感器和模拟执行器闭环，Nano、MTF-02P和真实动力暂不参加。

1. 保存PX4当前参数备份、`ver all`和现有接线照片。
2. 拆除全部桨叶，断开4S；只用USB给飞控供电。断开Nano和MTF-02P串口，防止真实与模拟传感器混入同一估计器。
3. 在QGC中建立HITL专用参数状态：启用HITL并选择兼容的HIL Quadrotor X机架。该步骤会改变飞控配置，不能省略；真实飞行前必须恢复生产参数备份。
4. 重启飞控，确认仍无4S动力电源，再退出QGC，避免QGC与AirSim同时占用Windows串口。
5. 复制当前AirSim `settings.json`为HITL专用配置。将PX4车辆改为串口连接：`UseSerial=true`，填写飞控实际COM号和波特率；关闭原SITL的TCP/LockStep连接。
6. 启动AirSim Blocks。AirSim通过同一MAVLink串口向PX4发送HIL传感器数据，并读取PX4模拟执行器输出；QGC应通过AirSim转发的UDP链路观察，不能再次直接打开被AirSim占用的COM口。
7. 首先只检查姿态、模拟高度、HIL传感器更新和执行器输出是否持续变化。确认虚拟机体静止时估计稳定，再进行虚拟解锁和低高度悬停。
8. 记录AirSim日志与PX4 ULog，检查时间跳变、传感器超时、EKF复位、执行器饱和和仿真暂停后的failsafe。
9. 测试结束后关闭AirSim、断开USB，恢复生产参数备份；核对`SYS_HITL`已关闭、机架与电机映射已恢复，才允许重新接4S。

首跑可暂用AirSim通用四旋翼动力学，重量、惯量、推力曲线和阻力参数可以后续标定；但HITL启用状态、HIL机架、串口、波特率、系统ID和端口归属必须在首跑前正确配置，不能全部留到以后。

### 第 6 层：Nano 实际算法接入

Nano 上的软件按输入适配、状态估计质量、任务状态机和命令输出分层：

```text
sim_camera / gc4653_camera ─┐
sim_flow / MTF-02P flow ───┼→ 统一带时间戳观测接口
sim_range / MTF-02P range ─┤
sim_uwb / future_uwb ──────┘
                            ↓
                 定位质量与任务状态机
                            ↓
              有界局部位置/速度目标 → PX4
```

仿真与实机只替换输入适配器，不替换任务状态机和 MAVLink 输出层。所有输入都必须带新鲜度、质量和协方差；失效时输出停止/保持请求，禁止沿用最后一次有效速度。

## 必须记录的数据

- AirSim 真值位置、速度、姿态和碰撞事件；
- 模拟/实机传感器时间戳、更新率、延迟、质量和丢包；
- PX4 EKF 状态、局部位置、创新量、复位和 failsafe；
- Nano 状态机阶段、输入年龄、输出 setpoint 和拒绝原因；
- PX4 actuator 输出，仅作为模拟动力学输入；
- QGC 消息、PX4 ULog、AirSim MAVLink 日志和版本信息。

## 下一步执行顺序

1. 在QGC导出当前飞控参数；保存现有`ver all`、接线基线以及厂商原版1.16.2固件来源。
2. 拆桨、断开4S、断开Nano与MTF-02P，只保留飞控USB；再次用插拔确认飞控为COM16。
3. 在QGC选择“高级设置→自定义固件文件”，刷写`测试备份/micoair_h743-v2_default-px4-1.16.2-hitl.px4`。
4. 重连后先核对`ver all`、`pwm_out_sim status`和基础传感器状态；此时仍不接4S。
5. 建立HITL专用参数状态并重启飞控，再生成AirSim COM16串口配置；QGC与AirSim不得同时直接占用COM16。
6. 用Blocks完成实机PX4首轮HITL：只验证HIL传感器输入、模拟执行器回传、虚拟机体运动和断流保护。
7. 首轮通过后再接Nano，最后导入比赛场地低细节版和定位故障模型。
6. 在 SITL 中实现并比较定位方案 A 和模拟 UWB 方案 B。
7. 最后评估 MicoAir H743 V2 + PX4 1.16.2 的 AirSim HITL 兼容性，再决定使用 AirSim HITL 或 Gazebo HITL。

## 开始下一阶段需要提供或确认的事项

### 用户提供的资料

1. **比赛场地**：官方尺寸图、CAD/FBX/OBJ/UE工程、机库位置与朝向、观察区、禁入区和高度限制；资料未发布时先提供能确认的尺寸草图。
2. **任务条件**：起点、必须经过区域、终点/观察区、最大用时、允许的定位设施，以及动态障碍是否属于正式规则。动态球需提供直径、数量、速度范围、来向、发射频率和有效区域。
3. **WSL/PX4信息**：在WSL中提供PX4源码绝对路径、`git describe --always --dirty`、发行版版本和上次成功启动AirSim SITL的完整命令。
4. **仿真相机目标**：Nano算法实际输入的分辨率、像素格式、帧率和视场角；若算法尚未确定，先以当前GC4653输出和计划网络输入尺寸分别记录。
5. **飞控连接信息**：Windows设备管理器中的飞控COM号；HITL时Nano是否仍通过TELEM1、QGC通过哪个链路连接。
6. **UWB资料**：正式发布后提供接口手册、输出消息、坐标系、更新率、延迟、协方差/质量字段和基站布置要求。
7. **整机约束**：预计总重、轴距、桨尺寸、最大推重比或至少电机/桨/电池型号，用于建立近似动力学模型。

### 用户先进行的操作

1. 在WSL执行并保存：发行版信息、PX4路径、Git版本和历史启动命令；不要先升级PX4源码。
2. 确认UE4.27能打开`Blocks.uproject`，启动后AirSim窗口不报插件或地图错误。
3. 复制备份`C:\Users\chaos\Documents\AirSim\settings.json`，保留原SITL配置。
4. HITL前拆桨并断开4S；确认USB供电不会给ESC提供可驱动电机的主电源。
5. 将上述终端输出、场地资料和COM端口截图发回，再生成与本机完全匹配的启动配置和检查脚本。

### 我可以继续完成的工作

- 审核并生成AirSim SITL/HITL的分离配置、启动脚本和日志采集脚本；
- 为比赛场地建立UE4导入清单、坐标标定和低细节碰撞版；
- 构造IMU、光流、ToF、相机和模拟UWB的噪声、延迟、丢包适配器；
- 让Nano使用统一仿真/实机传感器接口，并记录输入新鲜度和输出目标；
- 建立动态球场景、可行性标签、最近距离、延迟和安全降级测试；
- 分析PX4 ULog、AirSim日志和Nano日志，判断问题位于感知、定位、规划、控制还是通信。

## 当前最近目标

完成一次可复现的`Blocks + PX4 SITL + Nano`闭环：Nano给出有界局部速度目标，PX4控制虚拟四旋翼移动并悬停；停止Nano输出后0.5秒内目标归零，虚拟无人机进入预定安全状态。完成这一门禁后再导入比赛场地和模拟定位源。

## 2026-10-03 实机飞控 HITL 执行器链路验证

当前配置为 MicoAir H743 V2 实机飞控、HITL 专用固件、`SYS_AUTOSTART=1001`、`SYS_HITL=1`，AirSim 通过 COM16 与飞控通信，QGC 通过 AirSim 转发的 UDP 14550 连接。测试期间拆除桨叶、断开 4S、Nano 与 MTF-02P 暂不接入。

已确认：

- 未解锁时 `actuator_outputs_sim` 16 路均为 `0`，HIL 电机 1～4 的通道值为 `900`；
- QGC 可以正常解锁，`commander status` 为 `Armed`、`Hold`、`in failsafe: no`；
- 解锁后 `actuator_outputs_sim` 前四路出现怠速输出 `[0.002, 0, 0.002, 0]`；
- `pwm_out_sim status` 显示 HIL_ACT 通道 0～3 分别映射到电机 1～4（功能 101～104），通道值由未解锁的 `900` 提升为 `1002/1000/1002/1000`；
- 因此“实机 PX4 控制与分配 → 模拟执行器输出 → AirSim”链路已经打通。

下一门禁是先用 `pwm_out status` 和 `dshot status` 确认真实输出驱动未运行，然后在 QGC 中执行 1 m 低高度虚拟起飞、悬停和降落。起飞过程中记录 `commander status`、`vehicle_local_position`、`actuator_outputs_sim` 和 AirSim 画面；若出现拒绝解锁、failsafe、估计器复位或机体异常翻转，立即降落/停机并保留报错，不绕过检查。

### 首次虚拟起降结果

首次虚拟起飞、悬停、改变高度、降落及解除锁定均正常：

- 飞行中 `commander status` 为 `Armed`、`Hold`、`in failsafe: no`；
- 局部位置约为 `x=-0.053 m`、`y=-0.050 m`、`z=-2.294 m`，水平与垂直速度均接近 0；
- `xy/z/v_xy/v_z` 均有效，`heading_good_for_control=True`，全局位置有效且未进入航位推算；
- 悬停时四路模拟电机输出约为 `[0.492, 0.491, 0.492, 0.491]`，数值均衡；
- 降落并解除锁定后 `commander status` 恢复 `Disarmed`，16 路 `actuator_outputs_sim` 全部归零。

结论：AirSim 模拟传感器 → 实机 PX4 状态估计与控制器 → 控制分配 → `pwm_out_sim` → AirSim 动力学的闭环已经打通，首轮高度控制和安全停机通过。`dist_bottom_valid=False` 表示当前没有有效的离地测距融合，不影响本次依靠模拟 GNSS/气压计完成的基础 HITL 起降；后续光流/ToF 专项测试需要单独启用并验证该链路。

随后通过 QGC 下达飞行指令，AirSim 中的虚拟无人机能够正常完成平移、盘旋、改变高度和悬停。由此确认 QGC → AirSim UDP 转发 → 实机 PX4 导航与姿态控制 → 模拟控制分配 → AirSim 机体运动的正常控制闭环通过。下一阶段停止重复正常机动，依次验证 AirSim/HIL 数据中断保护、QGC 链路中断影响、重新连接恢复以及 Nano Offboard 接管。

### 下一阶段传感器接入顺序

当前 AirSim/PX4 HITL 已包含模拟 IMU、磁罗盘、气压计和 GNSS。无 GPS 定位专项按以下顺序增加传感器：

1. 保留当前可飞配置作为基线，先加入下视 AirSim Distance Sensor，量程设为 `0.02～6 m`，方向设为下视，模拟 MTF-02P 的 ToF 原始数据源。
2. 先通过 AirSim RPC API 验证距离随高度变化。当前本机 AirSim 1.8.1 是修改版；其源码虽包含将距离直接注入 HIL 串口的代码，但同一实现明确说明 PX4 不支持这种模拟距离注入方式，实际测试还会破坏已验证的 QGC 转发链路。因此配置使用 `ExternalController=false`，不从 AirSim 内部直接注入 PX4。
3. 建立独立适配器或由 Nano 读取模拟距离后，经 TELEM1 发送标准 MAVLink `DISTANCE_SENSOR`；届时再验证 PX4 `distance_sensor`、`vehicle_local_position.dist_bottom` 与有效标志。
4. AirSim 的 OpticalFlow 图像不能直接等同于 PX4 光流传感器消息。建立适配器，将模拟图像/运动结果转换为带积分时间、质量和距离的 MAVLink `OPTICAL_FLOW_RAD`。
5. 在 GNSS 保持开启时先验证光流和测距数据质量，再分阶段关闭 GNSS：静止、低速平移、悬停、短距离航线、纹理不足和测距失效。
6. 最后让 Nano 获取模拟相机/光流/测距输入并通过 TELEM1 发送给实机 PX4，使仿真与实机复用同一传感器与控制接口。

已生成独立配置 `测试备份/AirSim-settings-HITL-ToF-COM16.json`。它不会覆盖当前 `Documents/AirSim/settings.json`，需要在飞机落地、解除锁定并关闭 AirSim 后再切换测试。

### Nano 首次重新接入 HITL 的串口问题

Nano 使用 `/dev/ttyS1 @115200` 运行 `mavlink_protocol_observer.py` 时立即报告 `invalid MAVLink prefix '248'`。同期 PX4 `mavlink status` 显示 TELEM1 对应实例实际运行在 `/dev/ttyS0 @57600`，两端波特率不一致是错误字节流的直接原因。HITL 机架切换后部分串口参数未沿用此前生产配置。由于 QGC/NSH 依赖 AirSim 转发，修改时必须保持 QGC 与 AirSim 正常连接并确保飞行器已解除锁定，在 NSH 中将 `SER_TEL1_BAUD` 设为 `115200`并保存；完成后再关闭 AirSim 和 QGC、重启飞控，并按“QGC先启动、AirSim后启动”的顺序恢复，检查实例显示 `/dev/ttyS0 @115200`。

此时 `distance_sensor` 和 `sensor_optical_flow` 均为 `never published` 属于预期：AirSim 内部直接距离注入已关闭，Nano 适配器尚未发送 `DISTANCE_SENSOR` 与 `OPTICAL_FLOW_RAD`。它们不能用于判断当前串口是否正常；串口门禁以 Nano 连续解析 PX4 HEARTBEAT、无 CRC/前缀错误，以及 PX4 对应 MAVLink 实例出现 Nano 心跳和非零 RX 为准。

波特率统一为 115200 后，Nano 在 30 秒内成功识别 `PX4 sys=1 comp=1`，收到 36 个 HEARTBEAT，以及持续的姿态、位置、GPS和 HIL 数据，脚本 `summary exit=0`，未再出现前缀或 CRC 错误，确认 PX4 → Nano 接收方向恢复。Nano 观察器默认每秒发送一次伴随计算机 HEARTBEAT；若在脚本结束后才查看 PX4，瞬时 `rx` 已回落到 0，不能据此判定反向链路失败。反向链路使用 `--request-autopilot-version` 验证，必须同时收到 `COMMAND_ACK result=0` 和 `AUTOPILOT_VERSION`。

反向验证已通过：Nano 在识别 PX4 后发送 `MAV_CMD_REQUEST_MESSAGE(512)`，约 42 ms 后收到 `AUTOPILOT_VERSION`，并收到 `COMMAND_ACK command=512 result=0`；30 秒测试正常结束。因此 Nano ↔ PX4 TELEM1 115200 双向MAVLink链路通过。

AirSim 下视距离RPC读取已通过，但首轮数据质量未通过融合门禁：地面附近10个样本约为 `0.066～0.617 m`；QGC显示约2.9 m高度时，多组样本约为 `2.95～3.65 m`，单组跨度可达约0.7 m。QGC高度是EKF相对高度，距离传感器读取的是随姿态指向第一个碰撞面的斜距，两者存在固定差异可以接受；当前短时波动过大，不能直接转发给PX4。下一步同时记录AirSim真值位置、速度、横滚/俯仰和距离统计，在完全静止及平坦表面分别测量，以区分飞行动作、地形碰撞网格与传感器安装位置造成的变化。

增强测试后确认波动不是飞行器运动造成：落地30个样本期间真值位置固定、速度为0、姿态为0，但只有23个非负样本；悬停期间真值高度仅缓慢变化约3 cm、横滚/俯仰小于0.15°，距离仍为 `2.886～3.427 m`，标准差 `0.1414 m`。本机AirSim 1.8.1源码的 `DistanceSimpleParams.hpp` 将 `uncorrelated_noise_sigma` 固定为 `0.002*100 = 0.2 m`，`DistanceSimple.hpp` 对每个样本无条件加入该高斯噪声；设置解析函数也未读取可配置噪声字段。落地负值和约0.14 m实测标准差与该实现一致。

因此原生距离输出暂不允许直接进入PX4 EKF。短期通信链路测试可以在桥接层执行量程拒绝、滑动中值与低通滤波，并明确标记为测试滤波；正式定位仿真应修改AirSim源码，使噪声标准差、频率、延迟和启动延迟可配置，将ToF噪声设为符合目标传感器实测的数据后重新构建AirSim。

当前 TELEM1 的 `Forwarding: On` 会把 USB/HITL 上的大量 `HIL_SENSOR`、`SYSTEM_TIME` 等数据转发给 Nano。Nano只需要PX4为Onboard链路生成的状态流，后续还要发送传感器与Offboard目标，因此应将对应 `MAV_0_FORWARD` 设为 `0` 并保存，重启后确认 `/dev/ttyS0 @115200` 显示 `Forwarding: Off`。

### AirSim 碰撞卡住与 QGC 重连问题

测试中出现虚拟机体卡入地形，随后 QGC 指令无法继续控制；关闭并重新启动 AirSim 后未自动恢复，关闭并重新打开 QGC 后也无法重新连接。该现象由两个状态叠加造成：碰撞后的 AirSim 物理状态需要重置；AirSim 1.8.1 的 QGC UDP 代理在 UDP 接收端退出后不一定自动重建。QGC 只通过 AirSim 的 UDP 14550 观察飞控，AirSim 关闭时 QGC 本来就无法连接实机飞控。

固定恢复顺序如下：

1. 能操作时先在 QGC 降落并解除锁定；不能操作时直接按 HITL 故障处理，保持拆桨和断开 4S。
2. 依次关闭 AirSim、QGC以及任何占用 UDP 14550 的诊断脚本。
3. 断开飞控 USB 数秒后重新连接，使 PX4 的 HIL 时间和串口会话完全复位。
4. 先启动 QGC，并确认手动 UDP 链路正在监听 14550；QGC 的 Pixhawk/USB 自动连接保持关闭，不能占用 COM16。
5. 再启动 AirSim，由 AirSim 独占 COM16，并在启动时创建指向 QGC 14550 的 UDP 转发。
6. 确认 QGC 重新出现飞控、AirSim 日志显示收到 PX4 heartbeat、PX4 定位有效以后才能再次解锁。

运行中的 QGC 一旦退出，恢复时也采用“关闭 AirSim → 先启动 QGC → 再启动 AirSim”的顺序。不要在 AirSim 仍运行时反复重开 QGC期待旧代理自行恢复。地形碰撞后应先使用 AirSim API 的 `reset` 重置车辆；当前未建立可靠重置脚本前，直接完成上述冷启动恢复。

### 可配置 ToF 版本首次构建结果

首次执行 `构建可配置ToF的AirSim.ps1` 时，ToF 源码补丁已写入，原文件已备份为 `DistanceSimpleParams.hpp.before-configurable-tof.bak`，AirSim 的 C++ 库也已成功编译并复制到 Unreal 插件目录。失败发生在最后的 Blocks 打包阶段：脚本向 `package.bat` 传入了带引号的路径，而该批处理内部会再次添加引号，导致双重引号破坏路径解析，继而出现 `UnrealBuildTool.exe not found` 与 `RunUAT.bat` 目录判断错误。这不是 ToF 源码或编译器适配失败。

构建脚本已修正参数传递，并增加 `-SkipLibraryBuild`。续跑时只执行打包，不重复修改源码或编译已经成功的库：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
& "E:\湘大\RM\逐曦\进度汇报\RM\27赛季无人机\控制链路测试\脚本\SITL仿真\构建可配置ToF的AirSim.ps1" -SkipLibraryBuild
```

成功门禁为终端显示 `BUILD OK`，且生成 `H:\VisualDrone\AirSim\builds-tof\Blocks\Blocks.exe`。新版本写入独立目录，不覆盖当前可工作的 AirSim。打包成功后使用可配置 ToF 的 settings 启动新程序，静止悬停采集30个样本；期望距离标准差接近配置的 `0.01 m`，量程内无负值，短时跨度优先控制在 `0.06 m`以内，再决定是否进入 Nano 到 PX4 的距离消息桥接。

新 Blocks 首次启动后，QGC 曾显示 `Not Ready`，起飞提示“飞行器位置未知”。运行日志确认新程序已成功打开 `COM16 @115200`、收到 PX4 心跳、取得 Home，并在启动约3秒后显示 `Got GPS lock`，所以新可执行文件与串口链路正常。日志中的 `No valid data from Accel/Baro/Gyro` 和 `ekf2 missing data` 出现在收到第一帧HIL数据之前，属于启动瞬态；只有持续出现才是故障。此次还发现生效的 `Documents\AirSim\settings.json` 仍缺少新加入的 ToF 噪声、频率和延迟字段。

固定恢复流程：关闭 AirSim 和 QGC，复制 `AirSim-settings-HITL-ToF-COM16.json` 到 `Documents\AirSim\settings.json`，断开飞控USB数秒后重接；先启动 QGC 的 UDP 14550 监听，再启动 `builds-tof\Blocks\Blocks.exe`。看到GPS锁定后继续等待PX4估计器建立位置，确认 `vehicle_local_position` 的 `xy_valid/z_valid/v_xy_valid/v_z_valid` 均为 `True`、`vehicle_global_position` 时间戳持续更新且 `failsafe_flags` 的位置无效项为 `False`，才允许解锁或起飞。若位置仍无效，保留解除锁定状态并采集 `commander status`、`vehicle_local_position`、`vehicle_global_position`、`failsafe_flags` 和 `ekf2 status`，不得反复强行起飞。

冷启动后的复查已经确认位置链路恢复：`vehicle_local_position` 的四项位置/速度有效标志均为 `True`，`xy_global/z_global=True`；`vehicle_global_position` 的经纬度与高度有效，时间戳持续更新；`failsafe_flags` 中本地位置、速度、全局位置和Home均有效，Commander未进入failsafe。因此此前QGC的“飞行器位置未知”不是持续的HITL/GPS故障。当前剩余异常是 `heading_good_for_control=False`，同时遥控和Offboard信号丢失标志符合未接入相应控制源时的预期。若QGC仍显示 `Not Ready`，下一步读取 `estimator_status_flags`、`vehicle_status` 并执行只读的 `commander check`，以航向/解锁检查的明确失败项为准；不得用强制解锁绕过。

进一步检查显示 `estimator_status_flags` 的倾角、航向、GNSS位置、GNSS速度、磁航向、气压高度和GPS高度融合均有效，所有相关拒绝/故障标志均为 `False`；`vehicle_status` 显示 `hil_state=1`、`safety_off=True`、`power_input_valid=True`、`pre_flight_checks_pass=True`，并且 `commander check` 返回 `Preflight check: OK`。因此 `heading_good_for_control=False` 在当前静止地面状态下不是预检阻断项。若QGC仍提示“飞行器位置未知”，故障位于AirSim到QGC的MAVLink位置消息转发或QGC当前Vehicle对象状态，下一门禁是在QGC MAVLink Inspector中确认系统1的 `GLOBAL_POSITION_INT`、`LOCAL_POSITION_NED` 和 `HOME_POSITION` 持续更新；不能再通过修改EKF参数处理该现象。

QGC MAVLink Inspector 随后确认系统1的 `GLOBAL_POSITION_INT (33)`、`LOCAL_POSITION_NED (32)`、`HOME_POSITION (242)` 和 `GPS_RAW_INT (24)` 计数均持续增长，QGC恢复正常解锁和起飞。由此判定首次“位置未知”是启动/重连期间QGC位置状态尚未建立的瞬态，不是新Blocks、PX4 EKF或HIL串口适配故障。当前生效的 `Documents\AirSim\settings.json` 也已确认包含 `UncorrelatedNoiseSigma=0.01`、`UpdateFrequency=50`、`UpdateLatency=0.02` 和 `StartupDelay=0`，下一项测试转为验证重新构建后的ToF噪声是否实际下降。

新版本ToF首次数据表明噪声配置已生效，但射线几何尚未通过。静止样本为 `30/30` 有效，均值 `5.9994 m`、标准差 `0.0095 m`、跨度 `0.0425 m`；飞行样本为 `30/30` 有效，均值 `5.9988 m`、标准差 `0.0113 m`、跨度 `0.0387 m`。标准差已从旧版约 `0.14 m` 降至目标的约 `0.01 m`。然而两组数据在不同真实高度下都贴近配置的 `6 m` 最大量程，并允许噪声后略高于6 m，说明底层射线没有击中地面，当前输出只是“最大量程+噪声”，不能用于PX4或Nano定位。

`读取AirSim-ToF.py` 已修正验收逻辑：现在会打印RPC返回的传感器最小/最大量程、相对位置和相对姿态，并统计最大量程饱和样本；当至少80%样本位于最大量程以下 `0.05 m` 范围内时，测试明确返回 `TOF GEOMETRY CHECK FAILED`。下一步先用该输出确认运行时姿态确为下视 `Pitch=-90°`；若姿态正确，则问题位于当前Asian Village场景地面网格未响应距离传感器使用的 `ECC_Visibility` 射线，需更换有可见性碰撞的测试平面或修复场景碰撞后再验收。

改进后的5样本测试确认运行时量程为 `0.02～6.00 m`、相对位置为机体原点、相对姿态为 `Roll=0° / Pitch=-89.96° / Yaw=0°`，下视方向正确；5/5样本均在最大量程附近，脚本正确返回 `TOF GEOMETRY CHECK FAILED`。同期AirSim真值 `ned_z≈-0.537 m`，即使QGC下发的目标高度为3 m，也无法解释固定6 m输出；QGC目标高度本身也不能替代AirSim真值。由此确认问题是场景射线碰撞，而不是噪声、量程、安装方向或PX4高度估计。

构建脚本已加入距离射线的兼容回退：先保持AirSim原有的 `ECC_Visibility` 查询，未命中时再按 `WorldStatic + WorldDynamic` 对象类型查询，并继续忽略飞行器自身。下一版本应使用新的输出目录 `H:\VisualDrone\AirSim\builds-tof-rayfix` 打包，保留当前已验证可飞的 `builds-tof` 作为回退。若对象查询仍无法命中，说明该地图网格完全未启用Query Collision，届时在UE4场景中增加专用测试平面或修复网格碰撞，而不再修改传感器数学模型。

射线兼容版本验收通过。地面静止时运行时姿态仍为 `Pitch=-89.96°`，5个样本全部有效，平均距离 `0.1995 m`、标准差 `0.0059 m`、跨度 `0.0143 m`，最大量程饱和为 `0/5`，脚本返回 `TOF CHECK OK`。因此可配置噪声、下视安装方向、场景碰撞射线和RPC读取链路均已打通。当前数值与AirSim `ned_z` 不应直接相减比较：真值NED坐标相对仿真原点，ToF是沿传感器射线到首个碰撞面的距离；后续只需通过不同实际高度下的单调变化和稳定性验证，不要求二者具有相同零点。

高度响应复测通过。由于QGC引导起飞的最低目标高度为2 m，测试点选为QGC标称2 m和3 m：2 m点的ToF均值为 `2.2730 m`、标准差 `0.0078 m`、跨度 `0.0259 m`；3 m点的ToF均值为 `3.2918 m`、标准差 `0.0102 m`、跨度 `0.0319 m`，两组均为10/10有效且0饱和。同期AirSim真值NED高度变化约 `1.020 m`，ToF均值变化 `1.0188 m`，增量误差约 `0.0012 m`。两点均满足近似关系 `ToF≈-ned_z+0.758 m`，说明QGC/PX4高度与AirSim距离的差别是稳定坐标零点偏移，测距随真实高度变化的比例和方向正确。至此AirSim下视ToF仿真源通过，可进入“Windows仿真传感器适配器 → Nano → MAVLink DISTANCE_SENSOR → PX4”链路测试。

为控制磁盘占用，已删除被射线修复版替代的 `H:\VisualDrone\AirSim\builds-tof`，以及源码工程中可重新生成的 Blocks `Intermediate`、`Saved` 和插件 `Intermediate`，合计约5.2 GB。保留项包括当前可用的 `builds-tof-rayfix`、原始 `WindowsNoEditor` 基线、AirSim源码、Python环境、配置、脚本和全部测试记录。后续不再为每次小改动创建新的完整打包目录；同一阶段覆盖 `builds-tof-rayfix`，仅在形成新的稳定里程碑时保留一个独立版本。

### AirSim ToF 经 Nano 注入 PX4

PX4 执行 `listener distance_sensor 10` 返回 `never published` 符合当前状态。AirSim RPC 测距源已经通过，但 AirSim 未直接向 PX4 发布标准 `DISTANCE_SENSOR`，Nano 桥接进程也尚未运行。该结果不能用于否定 AirSim ToF 或 TELEM1 链路。

当前桥接链路为：

```text
AirSim DownwardDistance RPC
  → Windows airsim_tof_udp_sender.py
  → UDP 10.128.80.1:14660
  → Nano nano_tof_mavlink_bridge.py
  → /dev/ttyS1 @115200，MAVLink DISTANCE_SENSOR
  → PX4 TELEM1 /dev/ttyS0 @115200
  → uORB distance_sensor
```

已建立以下文件：

- `脚本/SITL仿真/airsim_tof_udp_sender.py`：只读取 AirSim RPC 并发送经过范围校验的 UDP 测距样本；
- `脚本/SITL仿真/nano_tof_mavlink_bridge.py`：只发送伴随计算机 `HEARTBEAT` 和 `DISTANCE_SENSOR`；PX4 心跳或 UDP 输入超时后停止发布测距；
- `脚本/SITL仿真/部署Nano-ToF桥.ps1`：上传 Nano 端脚本并执行离线 dry-run，不打开串口、不发送实时 MAVLink。

测试前确保飞行器落地并解除锁定，Nano 上没有运行 `mavlink_protocol_observer.py` 或其他占用 `/dev/ttyS1` 的进程。执行顺序如下。

Windows 部署：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
& "E:\湘大\RM\逐曦\进度汇报\RM\27赛季无人机\控制链路测试\脚本\SITL仿真\部署Nano-ToF桥.ps1"
```

若脚本在输入密码后显示 `Connection closed by 10.128.80.1 port 22`，说明 TCP 22 端口可达，但 Nano 在认证或创建远程 shell 时终止了会话，文件尚未上传。先单独运行 `ssh -o ConnectTimeout=10 root@10.128.80.1 "echo SSH_OK; id; test -d /root/uav/bin && echo DIR_OK"`。只有出现 `SSH_OK`、root 身份和 `DIR_OK` 后才重新执行部署脚本；若仍立即关闭，重新启动 Nano 后先验证该命令，并在 Nano 本地终端检查 `free -m`、SSH 服务进程和 `dmesg`，不要反复启动桥接程序。

Nano 先启动接收与 MAVLink 桥接。以下命令必须在 Nano 的 SSH/Linux Shell 中执行，不能直接粘贴到 Windows PowerShell。使用单行形式，复制时不要包含终端显示的 `#` 或 `>` 提示符：

```sh
python3 /root/uav/bin/nano_tof_mavlink_bridge.py --device /dev/ttyS1 --baud 115200 --listen-port 14660 --duration 180
```

Windows PowerShell 直接远程启动的等价命令为：

```powershell
ssh root@10.128.80.1 "python3 /root/uav/bin/nano_tof_mavlink_bridge.py --device /dev/ttyS1 --baud 115200 --listen-port 14660 --duration 120"
```

该 PowerShell 窗口需要保持打开；另开一个 PowerShell 窗口运行 Windows AirSim ToF 发送端。

随后在 Windows 启动 AirSim ToF 发送端：

```powershell
& "H:\VisualDrone\AirSim\python-env\Scripts\python.exe" `
  "E:\湘大\RM\逐曦\进度汇报\RM\27赛季无人机\控制链路测试\脚本\SITL仿真\airsim_tof_udp_sender.py" `
  --nano-host 10.128.80.1 `
  --nano-port 14660 `
  --rate 20 `
  --duration 60
```

两端运行期间，在 QGC 的 NSH 中检查：

```text
listener distance_sensor 10
mavlink status
```

通过条件：`distance_sensor` 持续发布，`current_distance` 随 AirSim 高度变化，范围为 `0.02～6.00 m`，`orientation=25`；Nano 日志中 `distance_sensor_tx` 持续增加且无大量拒绝；PX4 TELEM1 实例收到 `sysid=42, compid=191`。停止 Windows 发送端后，Nano 应报告 UDP 输入超时并停止发送新测距，PX4 主题时间戳随之变旧。此阶段只验证传感器通信，不修改 EKF 融合参数。

首次实时桥接已成功。PX4 `distance_sensor` 发布的设备来源为 `MAVLINK:0 (0x2A)`，对应 Nano 系统ID 42；`min_distance=0.02 m`、`max_distance=6.00 m`、`current_distance=3.31 m`、`orientation=25`、`variance=0.00010 m²`。TELEM1 实例 `/dev/ttyS0 @115200` 的接收速率约 `457 B/s`，识别到 `sysid=42, compid=191`，收到269条消息且丢包为0，消息132即 `DISTANCE_SENSOR`。因此 AirSim RPC → Windows UDP → Nano → TELEM1 MAVLink → PX4 uORB 的正向测距链路已经打通。仍需完成发送端停止后的超时停发，以及两个高度点在PX4端的变化复测，之后再讨论EKF融合。

桥接进程结束后的首次陈旧数据检查也符合预期：连续两次读取的 `distance_sensor.timestamp` 均为 `4131951099`，样本年龄由 `1237.196 s` 增至 `1240.732 s`，TELEM1 接收速率回到 `0 B/s`。这证明PX4没有把最后一个距离样本伪装成新数据。由于当时Nano桥接进程也已结束，该结果还不能单独证明“桥接仍运行但UDP源中断”的0.5秒看门狗；最终复测应让Nano桥接运行180秒、Windows发送端只运行60秒，并观察Nano明确输出UDP stale警告及发送计数停止。

最终复测通过。QGC标称2 m和3 m两个稳定高度点，PX4端 `distance_sensor.current_distance` 分别为 `2.30 m` 和 `3.30 m`，变化量恰为 `1.00 m`，方向与比例均正确；对应样本年龄仅约 `0.005 s` 和 `0.040 s`。Windows发送端停止且Nano桥接仍处于测试时段后，连续两次读取的最后时间戳均为 `6208744042`，样本年龄由 `27.772 s` 增至 `31.941 s`，距离保持 `3.29 m` 而没有产生新时间戳。因此正向传输、跨高度响应、量程/方向、TELEM1零丢包和输入断流停发均已验证。AirSim ToF → Windows UDP → Nano → MAVLink → PX4 uORB完整测距链路正式通过；下一阶段转入模拟光流消息桥接，当前不修改EKF融合参数。

### 实际场地模型导入 UE4.27

实际场地建模文件通常可以转换并导入当前 AirSim UE4.27 工程。优先使用 `FBX`；`OBJ` 可用但层级和材质信息较弱；`GLB/glTF`、`BLEND` 可先通过 Blender 转为 FBX；`STEP/STP/IGES/DWG` 等 CAD 文件应先通过 Datasmith 或建模软件转换、简化并生成网格；完整的 UE4.27 或更早版本 `.uproject/.umap/.uasset` 可迁移依赖，UE5 资产不能直接降级到 UE4.27。

导入前需要保留模型文件、全部纹理、单位或至少一个已知尺寸、场地原点和机库朝向。导入流程为：复制源文件备份；统一厘米单位和轴向；修复法线、材质与 UV；按飞行碰撞需求生成简化碰撞；在独立地图中设置 AirSim GameMode 和起点；用已知尺寸复核缩放；最后打包到当前 `builds-tof-rayfix` 阶段目录。地面、墙体和障碍物必须启用 Query Collision，并能响应 Visibility 或 WorldStatic/WorldDynamic 查询，否则 ToF、碰撞检测和规划都会得到错误结果。

当前工作区中没有发现用户的实际场地源模型。下一步只需提供模型文件的完整路径和扩展名；若文件较大，先提供文件夹截图、主文件名、扩展名、文件大小及是否有同目录纹理，即可确定转换工具和导入参数。

实际场地文件随后已放在 `H:\VisualDrone\AirSim\场地文件`。目录共有574个文件，其中68个STEP文件合计约0.456 GiB，并包含Creo `.asm.1/.prt.1`装配源。主要STEP文件为主装配 `rmuc2026_v2_0_0_asm.stp`（约174.5 MiB）、`jidi.stp`（约112.4 MiB）、`fu.stp`（约64.3 MiB）、`0006_1_1_prt.stp`（约49.6 MiB）和`feibiaozhan.stp`（约27.9 MiB）。该资料足以开始场地转换，但当前系统未发现FreeCAD、Blender、Assimp或MeshLab命令行工具。转换阶段优先从主装配生成中间网格，随后拆分地面/静态障碍/非碰撞装饰、降面并建立简化碰撞；所有处理输出写入新目录，原始场地文件保持不变。

### 下视光流源的第一阶段验证

本机AirSim 1.8.1源码和文档确认 `ImageType=8` 为 `OpticalFlow`，语义是双通道视场运动 `vx/vy`。UE4插件实际通过三通道8位图像缓冲返回该结果，因此现阶段不能假定通道顺序、零点、符号和比例，也不能直接把像素值写入MAVLink弧度字段。

已生成独立配置 `测试备份/AirSim-settings-HITL-ToF-Flow-COM16.json`，在已通过的ToF配置上增加名为 `DownwardFlow` 的下视相机：`Pitch=-90°`、`64×64`、视场角42°、关闭运动模糊。该视场角用于首轮编码标定，不代表最终MTF-02P光流镜头参数。还生成只读脚本 `脚本/SITL仿真/读取AirSim-OpticalFlow.py`，它只通过RPC读取类型8图像并输出三个原始缓冲通道的分位数，同时记录AirSim真值线速度、角速度、帧尺寸与帧间隔；不打开串口、不发送MAVLink或控制命令。

首次测试步骤：飞机落地并解除锁定后关闭AirSim和QGC，复制新配置到 `Documents\AirSim\settings.json`，按“QGC先启动、AirSim后启动”的稳定顺序重新连接；随后运行只读脚本。先采集静止样本，确认相机姿态、64×64帧、时间戳和原始通道零点，再分别采集已知X/Y平移与纯偏航数据。只有建立通道映射、比例、符号、积分时间和旋转补偿关系后，才实现Windows→Nano→`OPTICAL_FLOW_RAD`桥接。

静止样本首次测试通过基础门禁：`DownwardFlow`运行时姿态为 `Pitch=-89.97°`，30/30帧均为64×64，AirSim真值线速度和角速度均为0，三个原始缓冲通道所有像素也均为0，说明下视相机存在、类型8请求有效且静止零点无偏置。`simGetCameraInfo`报告约89.9°，该接口反映基础相机信息，尚不能证明类型8的42°捕获设置失效，后续通过已知运动比例校准实际焦距。RPC相邻帧时间为117～159 ms，多数约123～129 ms，即当前读取速率约8 Hz，低于实物光流约50 Hz；因此图像光流即使方向正确，也未必适合作为最终传感器源。下一门禁是在约2 m稳定高度进行单方向水平平移，同时记录真值速度和原始通道；若光流保持全零则放弃该渲染输出，若非零则继续标定。最终是否改用50 Hz解析光流模型，在已知运动测试后决定。

运动测试采用盘旋轨迹，虽然平移与偏航耦合，但足以验证光流源是否响应。160帧期间真值水平速度约从 `(0.393,-0.921)` 变化至 `(0.519,-0.863) m/s`，合速度接近1 m/s，偏航角速度约 `0.033 rad/s`；光流由静止全零变为第三缓冲通道约1、第二通道约0，第一通道始终为0。因此类型8光流确实对运动产生响应，第一通道可视为未使用通道。个别255与8位有符号值 `-1` 的补码表现一致，不能按无符号异常值处理。将采样间隔设为0后，帧间隔主要为24～30 ms，即约33～42 Hz；此前约8 Hz主要由脚本额外等待造成，当前速率可用于首轮HITL光流桥接。

只读脚本已更新为有符号8位解码，并增加机体系速度、航向、ToF距离、按42°视场计算的焦距及理论平移像素位移。后续盘旋或任意水平轨迹均可使用，不再要求偏航保持不变；先比较理论像素位移和两个有效通道，再确定通道映射与符号，随后才生成 `OPTICAL_FLOW_RAD`。

120帧盘旋复测进一步确认偏航变化不影响本阶段判断。脚本按每帧姿态把NED速度转换到机体系；有效119帧平均间隔为 `21.76 ms`，约46 Hz，平均下视距离 `2.239 m`。机体系横向速度主要集中在Y轴，`body_y=-1.034～-1.015 m/s`，X轴仅 `-0.115～-0.030 m/s`；航向从 `-155.01°` 变化到 `-149.93°`，平均偏航角速度 `0.0349 rad/s`。第一缓冲通道仍恒为0，第二有效通道仍因X轴运动过小而量化为0，第三通道稳定为正值约3，说明第三通道对负机体系Y速度响应，符号和运动方向稳定。

按配置的42°视场和针孔模型计算，理论平移约为 `0.835 px/帧`，AirSim编码第三通道平均为 `3.002`，比值约 `3.595`。因此类型8输出含有额外固定编码比例或使用了不同的内部投影尺度，不能把通道整数直接除以配置焦距后作为MAVLink弧度。当前不向PX4发布 `OPTICAL_FLOW_RAD`。为避免要求人工保持偏航，读取脚本增加了 `--camera-yaw-deg` 校准参数：它只在测试期间把下视相机绕垂直轴旋转，并在退出时自动恢复原姿态。下一次保持相同盘旋动作、临时设置相机Yaw为90°，用相同主运动激励另一图像轴；两轴映射和符号确认后，再决定采用经验比例还是真值驱动的50 Hz解析光流模型。

90°相机旋转复测完成，脚本明确输出 `original camera pose restored`。79个有效间隔的平均周期为 `24.11 ms`，平均距离 `3.230 m`，机体系平均速度为 `(0.141,-1.029) m/s`，航向变化 `3.75°`。两个有效通道均只在0、1、2等少数整数间变化，主通道均值 `1.042`，相对针孔理论值 `0.647 px` 的比例为 `1.610`；而前一组约2.24 m高度测试的比例为 `3.595`。两组比例随高度和量化区间大幅变化，证明AirSim类型8的8位缓冲不具备本项目所需的稳定线性标度。该图像输出只保留为渲染链路与运动响应验证，不再用作PX4定量光流消息源，也不再要求继续人工做定轴或定航向飞行。

后续采用50 Hz解析光流模型。新增Windows端 `airsim_flow_tof_udp_sender.py`：读取AirSim NED真值速度、机体系角速度和已通过的ToF距离，把线速度转换到FRD机体系，并依据MAVLink定义计算下视、机体对齐传感器的积分量：`integrated_x = gyro_x*dt - body_vy/distance*dt`，`integrated_y = gyro_y*dt + body_vx/distance*dt`。同时发送三轴积分角速度，供PX4进行旋转补偿。新增Nano端 `nano_flow_tof_mavlink_bridge.py`，它只允许发送 `HEARTBEAT`、`DISTANCE_SENSOR` 和 `OPTICAL_FLOW_RAD`，具有协议、范围、顺序、PX4心跳、UDP超时和串口独占检查，不包含解锁、模式、设定值或执行器接口。部署脚本为 `部署Nano-光流测距桥.ps1`。三份文件已通过Python语法检查和离线dry-run，尚未进行实时注入测试；首次注入只观察uORB消息，不修改EKF光流融合参数。

首次解析光流实时测试中，Windows发送端60秒统计为 `sent=0 / rejected=2999`，PX4端 `sensor_optical_flow` 和 `distance_sensor` 均未发布。该现象不是Nano桥没有启动：TELEM1实例 `/dev/ttyS0 @115200` 已收到 `sysid=42, compid=191` 的60个消息且丢包为0，恰好对应Nano桥每秒发送一次的心跳。因此已确认链路状态为“Windows样本校验全部拒绝 → 没有UDP传感器包 → Nano只发送心跳 → PX4没有光流/测距主题”；`cs_opt_flow=False` 是结果，不是根因。Windows脚本已增加逐项拒绝统计和最后样本输出，下一次先以 `--nano-host 127.0.0.1 --nano-port 14662 --rate 10 --duration 5` 做本机诊断。若原因为 `distance_saturated_or_out_of_range`，应移动到具有碰撞地面的有效位置或修复测距射线，不能放宽最大量程门禁，因为解析光流的比例直接依赖有效距离。

### RMUC2026实际场地首次导入

场地源目录确认同时包含Creo `.asm.1/.prt.1`和STEP文件。首轮选择完整总装 `H:\VisualDrone\AirSim\场地文件\场地文件\场地文件\rmuc2026_v2_0_0_asm.stp`（约174.5 MiB），而不直接读取原生ASM；STEP已固化装配几何，避免Creo零件版本和外部引用缺失。已从现有Blocks复制出独立工程 `H:\VisualDrone\AirSim\RMUC2026-UE4`，并启用AirSim、DatasmithImporter、DatasmithCADImporter、PythonScriptPlugin和EditorScriptingUtilities。原Blocks工程、当前可飞的打包版本和CAD源文件均未修改。

UE4.27 Datasmith接口探测通过。常规渲染模式首次导入在静态网格更新阶段触发Renderer TaskGraph访问异常；改用 `-nullrhi -nosound` 无渲染命令行模式后导入成功。结果写入 `/Game/RMUC2026/CAD` 和 `/Game/RMUC2026/Maps/RMUC2026_Field`，共1062个资产、1589个Actor、约307.2 MiB，其中1059个为几何资产，地图文件约2.54 MiB。自动脚本位于 `脚本/SITL仿真/导入RMUC2026场地到UE4.py`。当前仅完成CAD几何与装配层级导入，尚未通过可视检查、已知尺寸校验、原点/朝向调整和碰撞验收，因此不能直接替换现有HITL场景。下一阶段先在UE4编辑器打开独立地图，确认整体比例与轴向；再删除螺栓等无关细节、将地面/墙体/障碍设为可查询碰撞、设置AirSim GameMode和Player Start，并用ToF射线与无人机碰撞做验收，最后才打包新场景。

当前阶段继续使用AirSim UE4.27，不更换主仿真器。已有真实飞控HITL、QGC、Nano串口、ToF桥和解析光流适配器都围绕现有MAVLink链路完成，且实际场地已经成功进入UE4；此时迁移会重复建设串口HITL、传感器桥和场景。Gazebo可在后期作为第二套轻量回归环境，用于动力学、ROS和规划算法批量测试，但PX4官方当前对真实飞控HITL明确列出的外部环境仍是Gazebo Classic，并说明HITL属于社区维护，不能假定现有AirSim专用固件和桥接可直接复用。SIH适合快速验证控制和失联逻辑，但没有本项目所需的相机、光流和复杂场景传感器。Isaac Sim可在将来需要GPU深度相机、激光雷达或大规模感知数据时单独评估，当前不会改善已经打通的真实PX4硬件链路。

### RMUC2026完整Creo总装导入修正

首次使用的 `rmuc2026_v2_0_0_asm.stp` 不是完整比赛场地，原 `/Game/RMUC2026` 导入结果保留作对照，不能作为后续场景。完整源改为 `H:\VisualDrone\AirSim\场地文件\场地文件\rmuc2026-1.asm.5` 所代表的最新Creo总装。Creo装配内部引用的是无版本后缀的同目录文件，而原始资料把依赖零件放在下一层目录并使用 `.asm.N/.prt.N` 版本后缀；直接导入顶层ASM只得到空父节点，并报告 `机场.prt` 等依赖缺失。

为保持原文件不变，已建立 `H:\VisualDrone\AirSim\场地文件\CreoImportStaging`：从1177个Creo源文件中按基本文件名选择最高版本，复制为无版本后缀的1170个依赖文件，再从 `rmuc2026-1.asm` 导入。完整结果位于 `/Game/RMUC2026Full/CAD` 和 `/Game/RMUC2026Full/Maps/RMUC2026_Full_Field`，共2133个资产、2827个装配Actor，约0.366 GiB；其中2130个静态网格已设为 `Use Complex Collision As Simple`。只读复查确认关卡中的2144个静态网格组件全部为 `Query and Physics + BlockAll`，不是仅生成了碰撞数据却关闭组件响应。排除测试标记后的CAD包围盒约为29.01 m × 16.00 m × 3.63 m，与总装比例一致。

完整关卡已加入方向光、天空光和SkyAtmosphere，解决空关卡无光源导致的不可见问题。为避免AirSim在最终地面碰撞尚未逐项验收前穿过场地，加入了顶部位于CAD `Z=0` 的隐藏临时安全地面 `RMUC_TEMP_SAFETY_FLOOR_Z0`；它仅用于首轮检查，正式场景确定地面标高和碰撞后应删除。关卡还加入 `RMUC_ORIGIN_Z0`、正X和正Y方向标记，便于识别总装原点。

装配层级检查找到 `机场_PRT`，其唯一几何子节点包围盒中心约为 `(102.68, 61.20, -63.03) cm`，尺寸约 `59.41 × 59.51 × 119.36 cm`。已在其顶部上方75 cm放置候选出生点 `RMUC_HANGAR_PLAYER_START_CANDIDATE`，位置为 `(102.68, 61.20, 71.65) cm`，并加入只在编辑器中可见、无碰撞的候选标记。名称“机场”来自Creo装配，仍需在UE4中目视确认它是否就是比赛机库；确认前不把该地图设为项目默认启动地图，也不删除旧导入。

下一次人工检查：关闭并重新打开当前UE4编辑器以刷新由无界面进程写入的资产，打开 `/Game/RMUC2026Full/Maps/RMUC2026_Full_Field`。先检查光照、总装完整性、原点方向和 `机场_PRT` 候选位置，再用编辑器碰撞视图确认地面/墙体；确认机库后把PlayerStart移动到机库内部合适高度，删除或下移临时安全地面。只有无人机不穿地、ToF在地面和台阶上正确命中、场地已知尺寸复核通过，才将该地图设为AirSim默认场景并打包。

首次目视检查发现临时安全地面在编辑器中表现为贯穿整个场地的巨大平面。它虽然设置为运行时隐藏，但仍然具有实体碰撞，会把场地上下区域隔开；该临时方案不再保留。原点、正X、正Y和机库候选标记使用基础立方体创建，其中原点和方向标记最初仍有碰撞。修正脚本 `清理RMUC2026临时防坠与调试实体.py` 将删除安全地面，并把四个辅助标记设为运行时隐藏、无碰撞和仅编辑器使用；同时将方向光和天空光设为Movable、关闭预计算光照，避免2147个未构建光照对象警告。由于当前UE4编辑器打开着带星号的未保存关卡，不能让无界面进程同时覆盖该地图；需先保存需要保留的人工调整并关闭编辑器，再执行修正脚本。

AirSim默认 `BP_FlyingPawn` 实测包围盒为 `98.17 × 98.17 × 29.30 cm`，与目标 `100 × 100 mm` 相差约十倍。目标统一缩放系数为 `10/98.1694=0.101865`。只缩放机体网格会留下约1 m的碰撞和错误的相机/传感器安装点，因此没有采用视觉网格缩放。AirSim源码已加入车辆级 `PawnScale` 配置：生成Pawn后、初始化PawnSimApi和碰撞包围盒前统一执行Actor缩放；工程插件和AirSim源码副本均有原文件备份。配置副本 `AirSim-settings-HITL-RMUC2026-100mm-COM16.json` 已写入 `PawnScale=0.101865`。该补丁仍需在关闭UE4编辑器后重新编译插件，再复制配置并实测RPC碰撞尺寸。它会同步缩放视觉模型、螺旋桨、碰撞和相机挂点；AirSim内部质量、惯量、推力模型和PX4参数仍保持当前值，等真实机架参数确定后单独修改。

一键脚本首次执行时，UE4.27把包含中文的 `-ExecutePythonScript` 路径按ANSI代码页解析为乱码，产生Python语法错误；地图没有被修改，编译也未开始。脚本随后改为先将清理脚本复制到纯英文路径 `H:\VisualDrone\AirSim\RMUC2026-UE4\Saved\rmuc_cleanup.py`。修正后完整执行成功：日志显示 `removed_floor=1 / updated_markers=4 / updated_lights=2`；新的 `UE4Editor-AirSim.dll` 已生成；活动 `Documents\AirSim\settings.json` 保持 `COM16`、PX4Multirotor和ToF噪声0.01，同时启用 `PawnScale=0.101865`。下一次打开完整关卡时，临时防坠平面已不存在，四个定位标记只在编辑器中可见且无碰撞；运行时无人机视觉和碰撞目标尺寸约为10 × 10 cm。仍需通过一次运行时画面和狭窄障碍碰撞验证确认实际Pawn尺寸，随后再调整质量、惯量和轴距等动力学参数。

100 mm版本首次运行无法解锁。QGC只显示 `MAV_CMD_COMPONENT_ARM_DISARM temporarily rejected`，但AirSim日志给出实际原因：启动时 `FindTeleportSpot` 报告生成Actor与几何相交，随后PX4持续报告 `Preflight Fail: horizontal velocity unstable` 和 `Arming denied: Resolve system health failures first`。只读RPC确认串口连接、GPS 3D fix、有限的经纬高、气压计和IMU数据均存在，飞行器速度为0；碰撞记录对象为机库候选几何 `body_1_659`。根因是第一版缩放补丁在 `SpawnActor` 完成碰撞位置检查之后才调用 `SetActorScale3D`：UE先用约1 m默认Pawn寻找出生位置，发生相交或错误挪位，再把它缩至100 mm，污染EKF启动阶段。

源码已修正为在调用 `SpawnActor` 前构造带 `PawnScale` 的 `FTransform`，使出生位置调整、初始碰撞、PawnSimApi包围盒和后续视觉模型从第一帧起都使用100 mm尺寸。工程插件和AirSim源码副本均增加v2备份。该改动需要关闭UE4后重新执行 `应用RMUC2026场地修正与100mm无人机.ps1` 编译；编译完成后应关闭QGC和AirSim、断开飞控USB数秒并重新连接，按“QGC先启动、UE4/AirSim后启动”进行冷启动，避免沿用已经产生速度不稳定的EKF会话。只有新日志不再出现 `FindTeleportSpot ... intersecting geometry`，且 `commander check` 返回OK，才能再次尝试解锁。

v2补丁已在2026-10-05重新编译成功，新的 `UE4Editor-AirSim.dll` 已生成。包装脚本随后在备份 `settings.json` 时失败，原因是Windows PowerShell 5.1把无BOM UTF-8脚本中的中文绝对路径按本地代码页解码，路径显示为乱码；该错误发生在编译完成之后，不影响已生成的DLL。脚本现已改为从 `$PSScriptRoot` 推导测试目录，并保存为带UTF-8 BOM的文件；检查结果为 `EF BB BF`、PowerShell语法错误0。活动配置已经包含 `PawnScale=0.101865`、`COM16` 和 `PX4Multirotor`，因此无需为这次路径错误重复编译。下一步直接执行完整冷启动并验证启动碰撞和PX4预飞状态。

冷启动后飞行器可以正常解锁和起飞，但画面仍显示约1 m模型。AirSim RPC读取运行时对象确认 `PX4` 的Actor缩放为 `[1,1,1]`，证明并非镜头造成的视觉误判。原因是 `BP_FlyingPawn` 的Blueprint构造过程把Spawn Transform中的缩放恢复为1。v3源码同时采用三项处理：生成Transform继续携带 `PawnScale`；车辆使用 `AlwaysSpawn`，避免用临时的1 m模型执行出生位置调整；Blueprint构造完成后立即再次调用 `SetActorScale3D`，并在日志输出 `Applied PawnScale 0.101865 to vehicle PX4`。现有只读诊断脚本也加入 `vehicle_actor_scale` 输出。该源码需在关闭UE4编辑器后重新编译，运行后以RPC值接近 `[0.101865,0.101865,0.101865]` 为最终尺寸门禁。

v3重新编译和冷启动后，用户确认约100 mm无人机可正常解锁、起飞和飞行，场景尺寸与出生链路门禁通过。随后对解析光流Windows发送端执行5秒本机空端口诊断：有效ToF约 `3.01～3.05 m`，机体系速度和解析光流均为有限值，统计 `sent=29 / rejected=20`，有效输出约 `5.8 Hz`；拒绝项只有 `integration_time`，来自10 Hz诊断下两次AirSim RPC读取偶尔超过当前100 ms上限，不再出现此前导致 `sent=0` 的测距饱和/越界。由此可进入50 Hz实时传感器注入测试：先只确认PX4的 `sensor_optical_flow` 与 `distance_sensor` 持续更新，仍不修改EKF融合参数。

50 Hz实时注入首次成功。Windows端60秒统计为 `sent=2962 / rejected=37 / achieved_rate=49.4 Hz`，拒绝项仅为少量 `integration_time`，有效率约98.8%；ToF覆盖约 `1.79～3.79 m`。盘旋阶段Windows真值适配器记录到机体系速度约 `(0.178,0.431) m/s`，对应积分光流约 `(-0.00878,0.00323) rad`，说明解析量随水平运动明显变化。PX4端实时收到Nano `sysid=42, compid=191`，TELEM1接收约 `3713 B/s`、735条消息、丢包0；`sensor_optical_flow` 来源为MAVLink Nano，样本年龄约10 ms、积分周期16 ms、质量255、角增量和距离均可用、错误计数0；同步 `distance_sensor` 样本年龄约34 ms、距离2.03 m、方向25。至此解析光流与测距的正向发布链路通过。盘旋后读取的另一条光流样本年龄已经达到88秒，只能作为停发后数据陈旧证据，不能作为PX4端盘旋实时响应证据；最终门禁还需在发送期间读取一次运动样本，并在Windows停发而Nano仍运行的窗口内检查时间戳停止刷新和Nano `udp_age` 增长。

最终原始链路门禁通过。盘旋期间PX4连续读取到新鲜样本，年龄约 `14～35 ms`，`pixel_flow.x` 由悬停约 `0.00006 rad` 增至 `0.00519～0.01078 rad`，Z轴积分角度为 `0.00648～0.01295 rad`，符合盘旋中平移与偏航同时存在的状态；质量255、错误计数0、距离和角增量均有效。发送停止后，连续读取的光流时间戳保持 `2830052897`，年龄由 `5.932 s` 增至 `10.080 s`；测距时间戳保持 `2830054871`，年龄由 `7.497 s` 增至 `11.137 s`。因此运动响应、端到端实时发布和断流停发全部通过，解析光流＋ToF原始传感器链路正式结项。下一阶段进入EKF融合状态复核，先读取现有参数和援助源状态，不立即改参。

首次EKF状态读取尚不能判定融合通过。当前参数为 `EKF2_OF_CTRL=1`、`EKF2_RNG_CTRL=1`、`EKF2_HGT_REF=1`、`EKF2_GPS_CTRL=7`：光流融合已启用；测距为条件融合，仅在水平速度低于 `EKF2_RNG_A_VMAX`（默认1 m/s）且高度低于 `EKF2_RNG_A_HMAX`（默认5 m）时使用；高度参考仍为GPS。读取时 `cs_opt_flow=False`、`cs_rng_hgt=False`，光流援助源没有样本，测距援助源 `fused=False`；其 `time_last_fuse=2830049452` 比当前估计器时间约早398秒，说明传感器发送已经结束。此时本地位置有效来自GPS融合，不能作为光流融合证据；同时水平速度约1.01 m/s，刚好超过条件测距默认速度门限。下一次必须在Nano桥和Windows发送端仍运行、悬停速度明显低于1 m/s并持续至少数秒时读取融合状态。

第二次在传感器流实时运行期间复核，原始光流和测距均新鲜（年龄约6 ms和28 ms），但EKF仍显示 `cs_opt_flow=False`、`cs_rng_hgt=False`，光流援助源仍无样本，测距援助源 `fused=False`。当时盘旋速度为 `sqrt(0.65688²+0.75640²)≈1.002 m/s`，刚好高于条件测距默认上限1 m/s，并且 `cs_rng_kin_consistent=False`，所以测距未进入条件融合符合参数逻辑。QGC盘旋固定约1 m/s不是阻塞：融合门禁应改为Hold静止悬停，不需要盘旋。光流援助源完全无样本不能由速度门限解释，下一诊断点为PX4中间主题 `vehicle_optical_flow`/`vehicle_optical_flow_vel` 和 `sensors status`，确认数据是否从 `sensor_optical_flow` 通过VehicleOpticalFlow预处理模块进入EKF。

中间层诊断确认 `vehicle_optical_flow` 和 `vehicle_optical_flow_vel` 均未发布，`sensors status` 没有VehicleOpticalFlow条目，四个 `SENS_FLOW_*` 参数也未被标记为已使用；与此同时测距已成功进入条件融合：`cs_rng_hgt=True`、`cs_rng_terrain=True`、`cs_rng_kin_consistent=True`，援助源 `fused=True`、`innovation_rejected=False`、检验比约0.00006。源码和最终构建配置确认 `CONFIG_SENSORS_VEHICLE_OPTICAL_FLOW=y`，无需重刷固件。根因是PX4 `sensors` 模块只在未解锁状态下动态加入新传感器；本轮光流主题在飞行后才出现，因此预处理对象没有创建。恢复步骤为：保持Nano和Windows传感器流运行，先落地并解除锁定，等待至少2秒让 `sensors` 检测并启动VehicleOpticalFlow；先确认中间主题发布，再重新起飞检查EKF光流融合。

按“未解锁时先启动传感器流”复测后，`vehicle_optical_flow` 与 `vehicle_optical_flow_vel` 均持续发布，确认PX4预处理模块已启动。地面样本质量255、错误0，距离约0.023 m，低于预处理模块配置的最小地距0.08 m，因此只作为初始化验证。起飞后EKF门禁全部通过：`cs_opt_flow=True`、`cs_rng_hgt=True`、`cs_rng_terrain=True`、`cs_opt_flow_terrain=True`、`cs_rng_kin_consistent=True`，无光流、HAGL或位置创新拒绝。光流援助源 `fused=True`、`innovation_rejected=False`、双轴检验比约0；测距援助源在3.06 m处 `fused=True`、`innovation_rejected=False`、检验比约0.00003，二者最后融合时间均与当前时间同步。由此确认解析光流＋ToF不仅到达PX4原始主题，也已通过预处理并被主EKF实例实际融合。下一阶段先保持GPS开启，做融合持续性与轻微平移测试；稳定后再单独设计无GPS参数切换和恢复流程。

为减少重复输入，日常实时注入统一简化为“一端一条命令”，启动顺序仍为Nano先、Windows后。Nano单行命令同时完成两个IO复用寄存器设置、串口raw配置和180秒桥接；Windows单行命令以50 Hz发送120秒，留下约60秒用于观察断流。命令沿用现有脚本和安全门禁，不新增解锁、模式、设定值或执行器操作。

## 参考资料

- [AirSim PX4 SITL](https://microsoft.github.io/AirSim/px4_sitl/)
- [AirSim PX4 配置与 HITL](https://microsoft.github.io/AirSim/px4_setup/)
- [AirSim Settings](https://microsoft.github.io/AirSim/settings/)
- [PX4 Hardware Simulation](https://docs.px4.io/main/en/simulation/hardware)
