# 蓝莓多光谱检测样机 —— 固件交接包

> 打包日期:2026-08-21 | 固件 v1 | 物理验收通过(电机 / LED / 风扇 / 推杆全功能)

## 目录结构

```
zhunong_handoff_pkg/
├── 交接文档_HANDOFF.md       ← 先看这个:系统一句话、完整引脚图、特殊定义值、注意事项
├── 上位机通信协议.md         ← 协议全文档(二进制 AA55 / 短帧 / ASCII 文本)
├── 上位机开发建议.md         ← 给上位机开发者的指南(ACK 重试、采集流程)
├── 协议与接线说明.md         ← 接线细节汇总
├── 工程源码/                ← 完整 STM32 工程(CMake/Ninja 构建)
│   └── build/Debug/zhunong.hex   ← 已烧录固件(含电机/LED/风扇/推杆)
└── 上位机工具/              ← Python 上位机参考实现(pyserial)
```

## 系统架构(一句话)

STM32F407ZGT6 串口收命令 → 控制四类执行器:
- **步进电机**:HBS57H 闭环驱动器,STEP/DIR 脉冲,梯形规划+级联 PID,500rpm 硬限幅
- **风扇 / 三路 LED**:YYNMOS-4 四路 PWM(20kHz),短帧位掩码控制
- **升降门(电动推杆)**:XY-160D 双路 H 桥第 1 路,短帧控制,28s 超时保护

## 快速上手

### 1. 烧录固件(如已烧录可跳过)

```bash
cd 工程源码
rm -rf build                      # Windows 日期变更会导致 ninja 陈旧编译,必须删
cmake --preset Debug
cmake --build build/Debug
openocd -f interface/cmsis-dap.cfg -c "cmsis_dap_vid_pid 0xc251 0xf001" \
        -f target/stm32f4x.cfg -c "adapter speed 1000" \
        -c "program build/Debug/zhunong.hex verify reset exit"
```

> ⚠️ `cmsis_dap_vid_pid 0xc251 0xf001` 必须加:开发机有第二个 WCH 探针会干扰 OpenOCD。
> ⚠️ 烧录卡在 "Programming Started" 时:拔插 DAPLink 后再试(探针偶发失联)。

### 2. 串口通信

- 串口:115200-8N1,USART1(PA9=TX,PA10=RX),板载 CH340 → 电脑 COM 口
- 三类协议共存:**二进制 AA55**(上位机自动用)+ **短帧**(HEX 手输)+ **ASCII**(人工验证)

| 执行器 | 短帧命令 | 应答 | ASCII |
|---|---|---|---|
| 风扇 | `10 01` 开 / `10 00` 关 | `90 00` | `FAN 0~100` |
| LED | `12 掩码`(bit0-2=LED1-3) | `92 00` | `LED 0~7` |
| 推杆 | `11 00`升 `11 01`关 `11 02`停 | `91 00` | `DOOR 0/1/2` |
| 电机 | — | — | `EN` → `MOVE deg rpm` → `MOVE_OK` |

### 3. Python 工具

```bash
pip install pyserial
python hbs57h_protocol.py --port COMxx move 90 30   # 电机转 90°@30rpm
python hbs57h_protocol.py --port COMxx status        # 查状态帧
python motor_ctl.py status / stop / origin           # 电机控制封装
python verify_led2.py COMxx                          # LED/风扇/推杆协议回归测试
```

## 关键提醒(详细见交接文档)

- 48V 只接 HBS57H;电机动力/编码器线禁止带电插拔;XY-160D "+5V"接 3.3V;三地共地
- 改代码后行为对不上 → `rm -rf build` 全新编译
- 推杆装门后接限位开关(PC3=开到位, PC4=关到位 已预留)
- 电机断电重启位置归零 → 对好 1 号孔后发 `origin`
