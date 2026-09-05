# -*- coding: utf-8 -*-
"""LED 位掩码协议完整验证(修掉自动 STATUS 帧干扰的时序问题)"""
import serial, time

PORT = "COM41"

def crc16(data, crc=0xFFFF):
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

def bin_frame(cmd, payload=b""):
    body = bytes([cmd, len(payload)]) + payload
    c = crc16(body)
    return b"\xAA\x55" + body + bytes([c >> 8, c & 0xFF])

def parse_bin(buf):
    out = []
    i = 0
    while i + 4 <= len(buf):
        if buf[i] == 0xAA and buf[i+1] == 0x55:
            cmd, plen = buf[i+2], buf[i+3]
            if plen > 32: i += 1; continue
            if i + 4 + plen + 2 > len(buf): break
            body = bytes([cmd, plen]) + buf[i+4:i+4+plen]
            c = crc16(body)
            if buf[i+4+plen] == (c >> 8) and buf[i+4+plen+1] == (c & 0xFF):
                out.append((cmd, bytes(buf[i+4:i+4+plen])))
                i += 4 + plen + 2
            else:
                i += 1
        else:
            i += 1
    return out

ser = serial.Serial(PORT, 115200, timeout=0.5)
ser.reset_input_buffer()
time.sleep(0.6)

def drain():
    """清空输入缓冲(自动 STATUS 帧持续到达,只做固定次数读取即可)"""
    ser.timeout = 0.05
    for _ in range(4):
        ser.read(64)
    ser.timeout = 0.5

fails = 0
def check(tag, cond):
    global fails
    print(("OK  " if cond else "FAIL") + " " + tag)
    if not cond: fails += 1

def short_test(cmd, val, exp):
    drain()
    ser.write(bytes([cmd, val]))
    time.sleep(0.25)
    r = ser.read(64)
    check(f"短帧 {cmd:02X} {val:02X} → 回 {r[:2].hex(' ')}", r[:2] == exp)

def status_leds():
    drain()
    ser.write(bin_frame(0x08))
    time.sleep(0.2)
    r = ser.read(256)
    for c, pl in parse_bin(r):
        if c == 0x81 and len(pl) >= 18:
            return pl[14], pl[15], pl[16], pl[17]
    return None

def ascii_test(cmd, exp):
    drain()
    ser.write((cmd + "\r\n").encode())
    time.sleep(0.25)
    r = ser.read(64)
    check(f"ASCII {cmd!r} → 回 {r[:16]!r}", exp.encode() in r)

print("== 1. 短帧 LED 位掩码(文档 12 XX → 92 00) ==")
for val, exp in [(0x00, (0,0,0)), (0x01, (100,0,0)), (0x02, (0,100,0)),
                 (0x04, (0,0,100)), (0x03, (100,100,0)), (0x07, (100,100,100))]:
    short_test(0x12, val, b"\x92\x00")
    s = status_leds()
    check(f"    STATUS led={s[1:] if s else None} (应 {exp})", s is not None and s[1:] == exp)

print("== 2. 短帧风扇(文档 10 01/00 → 90 00) ==")
short_test(0x10, 0x01, b"\x90\x00")
s = status_leds(); check(f"   风扇占空比={s[0] if s else None} (应 100)", s and s[0] == 100)
short_test(0x10, 0x00, b"\x90\x00")
s = status_leds(); check(f"   风扇占空比={s[0] if s else None} (应 0)", s and s[0] == 0)

print("== 3. 二进制 AA55 LED_SET(0x12 掩码) ==")
for mask in (0x07, 0x05, 0x00):
    drain()
    ser.write(bin_frame(0x12, bytes([mask])))
    time.sleep(0.25)
    r = ser.read(256)
    acks = [pl for c, pl in parse_bin(r) if c == 0x80]
    ok = len(acks) == 1 and acks[0][0] == 0x12 and acks[0][1] == 0
    s = status_leds()
    expect = ((mask&1)*100, (mask&2)*100//2, (mask&4)*100//4)
    check(f"AA55 12 {mask:02X} → ACK={[x.hex(' ') for x in acks]}", ok and s is not None and s[1:] == expect)

print("== 4. ASCII LED 掩码 ==")
ascii_test("LED 7", "LED_OK")
s = status_leds(); check("   三灯全开", s and s[1:] == (100,100,100))
ascii_test("LED 0", "LED_OK")
s = status_leds(); check("   三灯全关", s and s[1:] == (0,0,0))
ascii_test("LED 8", "LED_ERR")

ser.close()
print("==== 结果:" + ("全部通过" if fails == 0 else f"{fails} 项失败") + " ====")
