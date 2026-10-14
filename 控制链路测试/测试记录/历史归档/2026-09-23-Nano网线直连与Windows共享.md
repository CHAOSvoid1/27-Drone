---
tags: [无人机, 测试记录, LicheeRV-Nano, 以太网, Windows-ICS]
status: USB开发链路已通过
stage: 04
date: 2026-09-23
hardware_revision: LicheeRV Nano-WE 70415
lichee_image: 2026-01-14-16-03-d4003f
---

# Nano 网线直连与 Windows 网络共享测试

阶段链接：[[../04-Lichee与MAVLink|04 LicheeRV Nano：烧录、网线开发与 MAVLink]] · 记录时间：2026-09-23 · 供电：电脑 USB-C → Nano Type-C · 网络：电脑 WLAN 共享到 Realtek 有线网口

## 本次目标

电脑连接 Wi-Fi `RM`，Realtek 有线网口直连 LicheeRV Nano-WE；通过 Windows Internet Connection Sharing 给 Nano 分配地址，随后从 PowerShell 查找 Nano IP。

## 接线

```text
Wi-Fi RM → 电脑 WLAN
电脑 USB → Type-C → Nano（供电）
电脑 Realtek RJ45 → 网线 → Nano-WE RJ45（网络）
```

## 实测过程

### 1. 物理链路与电脑接口

执行：

```powershell
Get-NetAdapter | Format-Table Name,InterfaceDescription,Status,LinkSpeed
Get-NetIPConfiguration
```

结果：

| 接口 | 设备 | 状态/速率 | IPv4 | 用途 |
|---|---|---|---|---|
| `WLAN`，Index 20 | Realtek RTL8852BE WiFi 6 | Up / 1.2 Gbps | `172.16.136.184` | Wi-Fi `RM`，共享来源 |
| `以太网`，Index 10 | Realtek PCIe GbE Family Controller | **Up / 100 Mbps** | **`192.168.137.1`** | Nano 直连，共享目标 |
| `以太网 3`，Index 19 | VirtualBox Host-Only | Up / 1 Gbps | `192.168.56.1` | 虚拟网卡，不用于 Nano |
| 蓝牙网络连接 | Bluetooth PAN | Disconnected | 无 | 不使用 |

判断：Nano 与电脑的 RJ45 物理链路已经建立，100 Mbps 符合 Nano 百兆网口；Windows 网络共享已把电脑有线端配置为 `192.168.137.1/24`。

### 2. ARP 表检查

执行：

```powershell
arp -a
```

`192.168.137.1` 接口下仅出现：

```text
192.168.137.255  ff-ff-ff-ff-ff-ff  静态
224.0.0.22       01-00-5e-00-00-16  静态
224.0.0.251      01-00-5e-00-00-fb  静态
224.0.0.252      01-00-5e-00-00-fc  静态
239.255.255.250  01-00-5e-7f-ff-fa  静态
```

未出现 `192.168.137.x` 的动态邻居，也未发现 Nano MAC/IP 映射。

### 3. 长时间上电后复测

Nano 已保持上电较长时间，再次执行：

```powershell
Get-NetNeighbor -InterfaceIndex 10 -AddressFamily IPv4
arp -a
```

InterfaceIndex 10 仍仅有以下永久项：

```text
239.255.255.250  01-00-5E-7F-FF-FA  Permanent
224.0.0.252      01-00-5E-00-00-FC  Permanent
224.0.0.251      01-00-5E-00-00-FB  Permanent
224.0.0.22       01-00-5E-00-00-16  Permanent
192.168.137.255  FF-FF-FF-FF-FF-FF  Permanent
```

没有出现任何动态单播 IPv4/MAC 项。长时间等待没有改变结果，排除“仅仅启动较慢或 DHCP 尚未来得及完成”这一简单解释。

### 4. 指示灯与 USB 枚举

- Nano 红灯常亮。
- 蓝灯有规律闪烁，目测约 1 秒一次。
- `Get-CimInstance Win32_SerialPort` 只列出 COM4/5/10/11/12/13，均为蓝牙串行端口。
- `Get-PnpDevice` 没有列出 Nano 对应的 RNDIS、USB Ethernet 或 USB Serial；列出的 USB Composite Device 不能仅凭名称认定属于 Nano。

判断：供电和板上程序活动迹象存在，蓝灯规律闪烁可能来自系统心跳/启动演示，但指示灯不足以证明 Linux 网络栈已正常工作。普通 Type-C 连接当前没有提供可识别的 Nano 控制台或 USB 网络通道，下一步改用排针 UART0。

补充判断：官方文档说明有线连接默认使用 DHCP，并提供网线、Wi-Fi、USB RNDIS 三种首次访问方式。因此当前证据不足以断言“以太网口默认未开启”；在购买 USB-UART 前，先从 SD 卡第一个分区创建 `usb.dev`、`usb.rndis0`（可同时创建 `usb.GS0`）并测试 USB RNDIS。若 RNDIS、普通路由器 DHCP 与预配 Wi-Fi 均失败，再将 UART0 作为确定性的救援入口。

### 5. SD 卡启动分区启用 USB Device

SD 卡第一个 Windows 可读分区为 `F:`。已执行：

```powershell
New-Item F:\usb.dev -ItemType File -Force
New-Item F:\usb.rndis0 -ItemType File -Force
New-Item F:\usb.GS0 -ItemType File -Force
Remove-Item F:\usb.host -Force -ErrorAction SilentlyContinue
Get-ChildItem F:\usb.*
```

结果存在五个零字节标志文件：

| 文件 | 来源/含义 |
|---|---|
| `usb.dev` | 本次新增，选择 USB Device 模式 |
| `usb.rndis0` | 本次按当前官方文档新增，申请 RNDIS 功能 |
| `usb.GS0` | 本次新增，申请 CDC ACM/GS0 功能 |
| `usb.rndis` | 镜像原有，可能是当前镜像使用的 RNDIS 标志名 |
| `usb.ncm` | 镜像原有，申请 NCM USB 网络功能 |

判断：文件创建位置和零字节形式正确。原镜像已包含 `usb.rndis` 与 `usb.ncm`，暂时全部保留，避免在尚未确认 20260114 镜像脚本识别哪个名称前删除有效标志。下一步安全弹出 SD 卡、插回 Nano、使用确认支持数据的 Type-C 线启动，然后比较 Windows 新增的 USB/网络/COM 设备。

### 6. 启用标志后重启枚举

为排除接口混淆，本次拔掉 RJ45，只保留 microSD 与 Type-C。重启后执行 `Get-NetAdapter`、`Get-NetIPConfiguration`、`Get-CimInstance Win32_SerialPort` 和 USB/PnP 查询。

实测结果：

- 没有新增 RNDIS/NCM/USB Ethernet 网络适配器。
- 没有新增 CDC ACM/USB Serial COM 端口；COM4/5/10/11/12/13 仍全部属于蓝牙。
- `USB\VID_30C9&PID_00A6` 对应电脑 Integrated Camera / USB Composite Device，不属于 Nano。
- Realtek 有线网口变为 `Disconnected`，符合本次已拔掉 RJ45 的接线状态，不能据此评价 Nano 以太网。

判定：启动分区标志创建正确，但 Nano USB Device 尚未被 Windows 枚举。现阶段无法区分“Type-C 线只供电/USB口问题”和“板端 gadget 服务未启动”；先用已知能传手机文件的数据线与另一个电脑 USB 口做 A/B 排查。确认线缆无误后仍不枚举，再尝试启动分区预配置 Wi-Fi或UART0。

### 7. 更换 Type-C 数据线后成功枚举

更换一根支持数据传输的 Type-C 线后，`Get-CimInstance Win32_SerialPort` 新增：

```text
COM14  USB 串行设备 (COM14)  USB\VID_359F&PID_2120&MI_00\...
```

判定：原 Type-C 线仅能供电或数据通道不可用，是前一次 USB Device 完全不枚举的直接原因。`VID_359F&PID_2120` 对应本次 Nano USB Gadget，`usb.dev`/`usb.GS0` 已生效，系统至少已运行到创建 USB Gadget 的阶段。下一步尝试 COM14 CDC ACM 控制台，并检查同一 VID/PID 下是否还有 RNDIS/NCM 子设备需要安装驱动。

### 8. USB NCM 网络成功枚举

同一 `VID_359F&PID_2120` 复合设备包含：

| 子设备 | 状态 | Windows 名称 |
|---|---|---|
| CDC ACM | OK | USB 串行设备 `COM14` |
| USB NCM | **OK** | `以太网 4 / UsbNcm Host Device` |
| RNDIS | Error | Remote NDIS based Internet Sharing Device |
| Composite | OK | USB Composite Device |

`以太网 4` 状态为 `Up`，Windows 报告链路速率 426 Mbps。该数值是 USB 虚拟网络适配器的报告值，不代表 Nano 的应用层实测吞吐率。

判定：USB 网络入口已经建立，RNDIS 子设备错误不再是当前阻塞项，因为 NCM 已正常工作。暂不修改 RNDIS 驱动，避免破坏已工作的 NCM。下一步查询 `以太网 4` 的 IPv4；按官方规则，电脑获得的 USB 网卡 IPv4 最后一段替换为 `1`，即为 Nano 候选地址，然后验证 Ping、TCP 22 和 SSH。

### 9. USB NCM、SSH 与系统基线通过

电脑端 `以太网 4 / UsbNcm Host Device` 获得：

```text
电脑：10.128.80.100
Nano：10.128.80.1
```

验证结果：

| 项目 | 结果 |
|---|---|
| Ping | 4/4 回复，0% 丢包，<1 ms |
| TCP 22 | `TcpTestSucceeded : True` |
| SSH | `ssh root@10.128.80.1` 成功 |
| SSH 主机指纹 | ED25519 `SHA256:BSyLpDmmEKFnzDjCOJOlES3+nGIaxChTbuyHwV1BITc` |
| 架构 | `riscv64`，确认 C906 镜像 |
| 内核 | `5.10.4-tag- #14 PREEMPT Thu Jan 22 14:08:28 CST 2026` |
| 系统 | Buildroot 2023.11.2，版本 `-gd4003f15b` |

输入密码进入 shell 后又输入了一次 `root`，因此出现 `-sh: root: not found`；这只是把用户名当成命令执行，不影响登录结果。

板端网络：

```text
usb1  UP  10.128.80.1/24   # 当前 Windows NCM 开发链路
usb0  UP  10.128.81.1/24   # 另一 USB 网络功能
eth0  UP/NO-CARRIER，无地址
wlan0 DOWN，保留 192.168.110.155/24，默认路由 linkdown
```

`eth0` 标志包含 `UP`，当前 `NO-CARRIER` 是本次 USB 测试已拔掉 RJ45 的结果，不能解释为以太网口没有启用。此前插网线时双方已经协商到100 Mbps。

存储：根分区 `/dev/mmcblk0p2` 为 29.0 GiB，已用 1.1 GiB、可用 26.7 GiB；`/boot` 为16 MiB，已用11.6 MiB。当前镜像没有单独挂载 `/mnt/data`，项目目录应改放根分区，例如 `/root/uav` 或 `/opt/uav`。

内存：`free -h` 显示 Linux 总内存约127.8 MiB，已用39.4 MiB、available 82.4 MiB、无Swap。该值小于芯片标称256 MB，可能有内存分配给固件、多媒体/ION或其他保留区域；视觉开发按实测 available 和持续运行峰值预算，不能按256 MB全部可用计算。

### 10. 项目目录、SCP与密码尝试

已创建 `/root/uav/bin`、`lib`、`models`、`config`、`samples`、`logs`。`cat /root/uav/hello.txt` 正确输出 `LicheeRV Nano SSH OK`，证明SCP上传和板端读取通过。

目录时间为 `Jan 1 00:33`，说明系统时间尚未校准；后续日志、性能数据和MAVLink时间戳测试前必须先校时。

执行 `passwd` 时出现重复字符警告，但程序继续进入第二次输入，最终因 `Passwords don't match` 保持原密码。结论：当前失败原因是两次输入不一致，而不是警告本身直接阻止root。重新执行时需把同一个密码准确输入两遍，并以最终成功提示为准。

`command -v chpasswd` 无输出，确认镜像未提供该命令。`cp -p /etc/shadow /etc/shadow.before-password-change` 无报错通常表示备份成功，下一步用 `ls -l` 核对两个文件。

### 11. 密码策略与普通用户验证

- root密码已成功修改；确认 `too many similar characters` 只是警告，第二次输入一致即可接受。
- `/usr/sbin/adduser` 与 `/usr/bin/su` 均存在。
- 已执行 `adduser devuser`，密码成功设置，输出 `passwd: password for devuser changed by root`。
- 在root shell内执行不带用户名的 `su -` 仍然切换到root登录shell，不能验证普通用户。下一步应从新的PowerShell窗口执行 `ssh devuser@10.128.80.1`，然后用 `id`/`pwd`确认身份；需要管理权限时再执行 `su -` 并输入root密码。

设计决定：保留系统内置 `root` 名称，避免破坏Buildroot脚本和服务；日常账户使用普通用户名。若要把临时名称 `devuser` 换成个人名称，优先在尚未产生用户文件时删除后重建，不直接手工改 `/etc/passwd`、`/etc/shadow`、`/etc/group`。

后续执行 `deluser devuser` 返回 `unknown user devuser`，说明账户数据库中没有可删除的 `devuser`；此前的 `adduser` 输出不足以证明用户条目最终存在。用户决定当前开发只使用 `root`，不再修改用户名。后续以已修改的root密码、USB本地链路和保留SSH主机指纹作为访问基线。

### 12. 下一阶段测试顺序

1. 新PowerShell会话使用新root密码登录，确认旧会话之外也可访问。
2. 从Windows同步一次系统时间，解决日志显示Jan 1的问题。
3. 执行 `sync; reboot`，验证USB NCM、SSH、密码和 `/root/uav/hello.txt` 重启后仍存在。
4. 连续冷启动3次，每次记录NCM地址、启动到Ping/SSH可用时间。
5. 用SHA256对比一次SCP上传文件，确认传输完整性。
6. 运行10分钟持续Ping并同时保持SSH，记录丢包和断连。
7. 完成基线后进入 [[../05-GC4653视觉链路|05 GC4653视觉链路]]。

USB NCM只建立电脑与Nano的私有网络，不自动保证Nano访问互联网。当前可通过SCP传程序、库和模型；完整SDK和依赖下载留在PC/WSL。RJ45用于接入普通局域网、无USB数据线时远程维护、给多台设备访问或独立验证有线网络；当前台架开发可以暂时不接。最终机载形态不依赖RJ45，Nano由合规稳压供电并通过UART与飞控通信。

### 13. 重启、传输与稳定性实测

| 测试 | 结果 |
|---|---|
| `sync; reboot` | SSH正常断开，重启后可重新登录 |
| root新密码 | 重启后登录成功，确认持久化 |
| `/root/uav/hello.txt` | 重启后仍存在，内容正确 |
| 项目目录 | `bin/config/lib/logs/models/samples` 全部持久存在 |
| 10 MiB传输文件 | 板端SHA256为 `e5b844cc57f57094ea4585e235f36c78c1cd222262bb89d53c94dcb4d6b3e55d`；还需与Windows原始文件输出核对后才完成双端校验 |
| USB NCM持续Ping | 600发送/600接收，0%丢包，0～1 ms，平均0 ms |

判定：USB NCM、SSH、密码、根分区文件持久性和10分钟链路稳定性均通过。

### 14. 命令环境与时间同步纠正

用户在Nano的Linux `#` 提示符下执行了PowerShell语法 `$time = Get-Date ...` 和 `Remove-Item`，因此分别出现 `-sh: =: not found` 与 `-sh: Remove-Item: not found`。PowerShell命令必须在 `PS C:\...>` 提示符运行；Nano端删除文件使用 `rm`，且板端测试文件已经成功删除。

嵌套执行 `ssh root@10.128.80.1 "date -s '$time'"` 时，Linux shell中的 `$time` 为空，故出现 `date: invalid date ''`。板端时间仍为1970年。改用无空格的BusyBox时间格式，并从Windows PowerShell发送UTC时间：

```powershell
$utc = (Get-Date).ToUniversalTime().ToString('MMddHHmmyyyy.ss')
ssh root@10.128.80.1 "date -s $utc; date"
```

实测该镜像BusyBox `date` 不接受 `MMddHHmmyyyy.ss`，返回 `invalid date '092313432026.37'`，时间仍为1970年。改用ISO字符串并由PowerShell保留远端单引号：

```powershell
$utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss')
ssh root@10.128.80.1 "date -s '$utc'; date"
```

校时成功后再重启验证。若重启又回到1970年，则判定当前板卡/镜像不能保持RTC时间；台架阶段每次连接时由电脑校时，联网后再配置NTP。

实测确认重启后仍回到1970年。当前板卡/镜像没有可用的RTC保持或启动校时来源；所有带时间戳的测试须在每次启动后先由Windows发送UTC时间，或以后联网启用NTP。

### 15. 互联网测试

输入 `ping -c 3 1.1.1` 时，Linux将缩写IPv4解释为 `1.1.0.1`。规范地址应为 `1.1.1.1`。但当前 `ip route` 的默认路由仍指向 `wlan0` 的 `192.168.110.1` 且为 `linkdown`，所以即使改正地址也不会联网；`github.com: Try again` 同时说明DNS不可用。

这不影响USB私有网络开发。现阶段程序、库和模型由PC下载/交叉编译后通过SCP上传。需要板端互联网时，再单独配置Wi-Fi或RJ45默认路由，避免改动已稳定的NCM链路。

### 16. GC4653镜像支持预检查

| 项目 | 实测结果 |
|---|---|
| `sensor_test` | 存在，可执行，906760字节，2026-01-14 |
| `sample_vio` | 存在，可执行，951856字节，2026-01-14 |
| GC4653传感器库 | `/mnt/system/usr/lib/libsns_gc4653.so` |
| GC4653 ISP参数 | `/mnt/cfg/param/sipeed_gc4653_30fps_202403261356.bin` |
| 当前配置 | `/mnt/data/sensor_cfg.ini`，295字节 |
| 量产版候选 | `/mnt/data/sensor_cfg.ini.beta`，295字节 |
| 内测版候选 | `/mnt/data/sensor_cfg.ini.alpha`，296字节 |

判定：20260114镜像包含GC4653库、30 fps ISP参数、配置和官方测试程序，软件文件门禁通过。70415量产板应先确认当前配置与 `.beta` 完全一致。文件存在仍不能证明任意第三方GC4653模组的排线、供电和MIPI lane定义兼容。

## 结果

- 判定：**USB NCM 开发链路已通过；RJ45 DHCP 尚未独立复测。**
- 已通过：Type-C 供电后的网口物理链路；100 Mbps 协商；WLAN → Realtek 有线网口的 Windows 网络共享；电脑端 `192.168.137.1/24`。
- 已通过：Nano USB NCM 地址发现、ICMP、TCP 22 和 SSH。
- 待验证：重新插入 RJ45 后的 `eth0` 载波、DHCP与SSH；当前可先通过USB完成开发。
- 当前证据只说明以太网 PHY 已建立；Nano 没有在该链路上产生 Windows 可见的 IPv4 邻居。优先检查 Linux 是否完整启动、`eth0` 是否存在/启用、镜像是否自动运行 DHCP 客户端，而不是继续在 Windows 端等待或重复查询 ARP。

## 下一步

1. 检查 Type-C 是否同时枚举了串口或 USB 网络设备：

```powershell
Get-CimInstance Win32_SerialPort | Format-Table DeviceID,Name,PNPDeviceID
Get-PnpDevice -PresentOnly | Where-Object {
    $_.FriendlyName -match 'COM|RNDIS|USB Ethernet|USB Serial'
} | Format-Table Status,Class,FriendlyName,InstanceId
```

2. 使用外置 3.3V USB-UART：Nano `A16/TX → USB-UART RXD`、`A17/RX ← USB-UART TXD`、`GND ↔ GND`；Nano 继续由 Type-C 供电，USB-UART 的 VCC/5V/3V3 不连接。串口设置为 115200 8N1、无流控。
3. 查看完整启动日志并在 Nano 运行：

```sh
ip link
ip addr show eth0
ip route
udhcpc -i eth0
```

3. 如果镜像没有 DHCP 客户端或申请失败，临时设置：

```sh
ip link set eth0 up
ip addr flush dev eth0
ip addr add 192.168.137.2/24 dev eth0
```

4. 回到电脑验证：

```powershell
ping 192.168.137.2
Test-NetConnection 192.168.137.2 -Port 22
ssh root@192.168.137.2
```

## 待补证据

- [ ] Nano 上电与网口指示灯照片
- [ ] USB-UART 完整启动日志
- [ ] Nano `ip addr` / `ip route` 输出
- [ ] DHCP 或固定地址结果
- [ ] PowerShell `Test-NetConnection` 与 SSH 登录结果
