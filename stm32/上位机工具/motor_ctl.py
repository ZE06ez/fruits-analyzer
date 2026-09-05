# -*- coding: utf-8 -*-
"""
电机控制 + 实时监控脚本
用法:
  python motor_ctl.py move 3600 30     # 绝对角度 3600° @ 30rpm,实时监控到位
  python motor_ctl.py rel 22.5 10      # 相对转 22.5° @ 10rpm
  python motor_ctl.py stop
  python motor_ctl.py status
  python motor_ctl.py origin
  python motor_ctl.py pid 12 0.5 0.1   # 实时改位置环PID
python motor_ctl.py --port COM37 ...(默认 COM37)
"""
import sys, time, serial
import hbs57h_protocol as P

PORT = 'COM37'
SERIAL_TIMEOUT = 0.2

def parse_stream(buf):
    """增量组帧解析:把收到的字节流拼起来,返回完整帧列表"""
    global _pbuf
    _pbuf += buf
    frames = P.parse_response(_pbuf)
    if frames:
        # 计算已消费字节数:最后一帧结束位置
        consumed = 0
        for f in frames:
            consumed = _pbuf.find(b'\xaa\x55', consumed) + 4 + f['plen'] + 2
        _pbuf = _pbuf[consumed:]
    return frames

_pbuf = b''

def main():
    global _pbuf
    args = sys.argv[1:]
    port = PORT
    if args and args[0] == '--port':
        port = args[1]; args = args[2:]
    if not args:
        print("缺少命令"); sys.exit(1)

    ser = serial.Serial(port, 115200, timeout=SERIAL_TIMEOUT)
    ser.reset_input_buffer()
    _pbuf = b''

    def send(frame, label, monitor=0):
        ser.reset_input_buffer()
        _pbuf = b''
        print(f"发送: {label}  [{frame.hex(' ')}]")
        ser.write(frame)
        t0 = time.time(); last = 0; done = False
        while time.time() - t0 < (monitor if monitor else 2.0):
            b = ser.read(ser.in_waiting or 1)
            if not b: continue
            for f in parse_stream(b):
                if f['cmd'] == 0x80:
                    print(f"  ACK: cmd=0x{f['payload'][0]:02X} result={f['payload'][1]}"
                          f"({'OK' if f['payload'][1]==0 else 'ERR'})")
                elif f['cmd'] == 0x81:
                    s = P.decode_status(f['payload'])
                    if s['state'] == 3 and not done and s['pos_deg'] == s['tgt_deg']:
                        done = True
                        print(f"  ✅ 到位: {s}")
                    elif time.time() - last > 1.0:
                        last = time.time()
                        print(f"  运动中: 状态={s['state']} 位置={s['pos_deg']:8.1f}° 速度={s['vel_rpm']:5.1f}rpm")

    cmd = args[0]
    if cmd == 'move' and len(args) >= 3:
        send(P.cmd_move_abs(float(args[1]), float(args[2])), f"MOVE_ABS {args[1]}°@{args[2]}rpm",
             monitor=1 + abs(float(args[1]))/(float(args[2])*6) + 5)
    elif cmd == 'rel' and len(args) >= 3:
        send(P.cmd_move_rel(float(args[1]), float(args[2])), f"MOVE_REL {args[1]}°@{args[2]}rpm", monitor=20)
    elif cmd == 'stop':
        send(P.cmd_stop(), "STOP")
    elif cmd == 'status':
        send(P.cmd_query_status(), "QUERY_STATUS")
    elif cmd == 'origin':
        send(P.cmd_set_origin(), "SET_ORIGIN")
    elif cmd == 'pid' and len(args) >= 4:
        send(P.cmd_set_pos_pid(float(args[1]), float(args[2]), float(args[3])), "SET_POS_PID")
    elif cmd == 'vpid' and len(args) >= 3:
        send(P.cmd_set_vel_pid(float(args[1]), float(args[2])), "SET_VEL_PID")
    elif cmd == 'profile' and len(args) >= 4:
        send(P.cmd_set_profile(float(args[1]), float(args[2]), float(args[3])), "SET_PROFILE")
    else:
        print("未知命令")
    ser.close()

if __name__ == '__main__':
    main()
