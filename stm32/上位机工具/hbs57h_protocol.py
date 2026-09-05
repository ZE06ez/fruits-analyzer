# -*- coding: utf-8 -*-
"""
HBS57H 上位机协议测试脚本(与 STM32 固件 comm.c 完全一致的编码)
帧格式: AA 55 CMD PLEN [PAYLOAD] CRCH CRCL
CRC16-CCITT-FALSE (poly 0x1021, init 0xFFFF),覆盖 CMD..PAYLOAD,小端序

用法:
  python hbs57h_protocol.py                       # 自检(CRC向量+编码)
  python hbs57h_protocol.py --port COM35 move 90 30    # 转90度@30rpm
  python hbs57h_protocol.py --port COM35 stop
  python hbs57h_protocol.py --port COM35 status
  python hbs57h_protocol.py --port COM35 pid 12 0.5 0.1
需要: pip install pyserial
"""
import sys, struct

AA, SS = 0xAA, 0x55

def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc

def frame(cmd: int, payload: bytes = b"") -> bytes:
    body = bytes([cmd, len(payload)]) + payload
    crc = crc16_ccitt_false(body)
    return bytes([AA, SS]) + body + bytes([(crc >> 8) & 0xFF, crc & 0xFF])

def f32(x: float) -> bytes:
    return struct.pack('<f', x)

# ---- 命令构造 ----
def cmd_move_abs(deg: float, rpm: float) -> bytes: return frame(0x01, f32(deg) + f32(rpm))
def cmd_move_rel(deg: float, rpm: float) -> bytes: return frame(0x02, f32(deg) + f32(rpm))
def cmd_stop() -> bytes:              return frame(0x03)
def cmd_set_pos_pid(kp, ki, kd):      return frame(0x04, f32(kp)+f32(ki)+f32(kd))
def cmd_set_vel_pid(kp, ki):          return frame(0x05, f32(kp)+f32(ki))
def cmd_set_profile(maxrpm, acc, dec):return frame(0x06, f32(maxrpm)+f32(acc)+f32(dec))
def cmd_set_config(ppr):              return frame(0x07, f32(ppr))
def cmd_query_status():               return frame(0x08)
def cmd_set_origin():                 return frame(0x09)
def cmd_reset():                      return frame(0x0F)

def parse_response(data: bytes):
    """解析 MCU 返回帧(可能多条,返回列表)"""
    out = []
    i = 0
    while i + 6 <= len(data):
        if data[i] != AA or data[i+1] != SS:
            i += 1; continue
        cmd, plen = data[i+2], data[i+3]
        if i + 4 + plen + 2 > len(data): break
        payload = data[i+4:i+4+plen]
        crc = (data[i+4+plen] << 8) | data[i+4+plen+1]
        ok = crc == crc16_ccitt_false(bytes([cmd, plen]) + payload)
        out.append({'cmd': cmd, 'plen': plen, 'crc_ok': ok, 'payload': payload})
        i += 4 + plen + 2
    return out

def decode_status(p):
    if len(p) < 14: return None
    state, err = p[0], p[1]
    pos, vel, tgt = struct.unpack('<3f', p[2:14])
    return dict(state=state, err=err, pos_deg=round(pos,2), vel_rpm=round(vel,2), tgt_deg=round(tgt,2))

def decode_info(p):
    if len(p) < 13: return None
    ver = p[0]
    ppr, maxrpm, acc = struct.unpack('<3f', p[1:13])
    return dict(ver=ver, ppr=ppr, max_rpm=maxrpm, acc=acc)

def self_test():
    # 标准测试向量 "123456789" -> 0x29B1
    assert crc16_ccitt_false(b'123456789') == 0x29B1, "CRC 向量错误!"
    # 与 C 固件完全一致的编码示例
    f = cmd_move_abs(90.0, 30.0)
    print(f"MOVE_ABS(90,30) = {f.hex(' ')}")
    print(f"CRC16 自检通过(标准向量 0x29B1) ✓")

def main():
    if len(sys.argv) >= 2 and sys.argv[1] == '--port':
        import serial
        port = sys.argv[2]
        args = sys.argv[3:]
        ser = serial.Serial(port, 115200, timeout=0.5)
        if args and args[0] == 'move' and len(args) >= 3:
            ser.write(cmd_move_abs(float(args[1]), float(args[2])))
        elif args and args[0] == 'rel':
            ser.write(cmd_move_rel(float(args[1]), float(args[2])))
        elif args and args[0] == 'stop':
            ser.write(cmd_stop())
        elif args and args[0] == 'status':
            ser.write(cmd_query_status())
        elif args and args[0] == 'pid':
            ser.write(cmd_set_pos_pid(float(args[1]), float(args[2]), float(args[3])))
        elif args and args[0] == 'vpid':
            ser.write(cmd_set_vel_pid(float(args[1]), float(args[2])))
        elif args and args[0] == 'profile':
            ser.write(cmd_set_profile(float(args[1]), float(args[2]), float(args[3])))
        elif args and args[0] == 'ppr':
            ser.write(cmd_set_config(float(args[1])))
        elif args and args[0] == 'origin':
            ser.write(cmd_set_origin())
        else:
            print("未知命令"); sys.exit(1)
        # 收应答
        buf = ser.read(64)
        for r in parse_response(buf):
            if r['cmd'] == 0x81:
                print("STATUS:", decode_status(r['payload']), "CRC:", "OK" if r['crc_ok'] else "BAD")
            elif r['cmd'] == 0x82:
                print("INFO:", decode_info(r['payload']))
            else:
                print(f"ACK/其他 cmd=0x{r['cmd']:02X} payload={r['payload'].hex()}")
        ser.close()
    else:
        self_test()

if __name__ == '__main__':
    main()
