# -*- coding: utf-8 -*-
"""
HBS57H 位置-速度双环级联 PID 参数仿真调优 v3
架构:
- 前馈:位置式梯形速度规划(加速/匀速/减速),决定速率
- 外环:位置环 PID,只在接近目标区做小修正(抗饱和+限幅)
- 内环:速度环 PI,平滑速率命令
- 到位:脉冲数到达目标即停(步进电机脉冲域精确跟随)
"""
import numpy as np

DT      = 0.001
PPR     = 1600.0

# ---- 待调参数 ----
KP, KI, KD  = 10.0, 0.2, 0.08     # 位置环 PID(修正量限幅 ±200pps)
KVP, KVI    = 0.6, 6.0            # 速度环 PI
ACC, DEC    = 2500.0, 2500.0      # 加/减速 pps/s
INT_ZONE    = 50.0                # 积分生效区(步)
CORR_LIM    = 200.0               # 位置环修正限幅 pps
VEL_I_LIM   = 300.0

def clip(x, lo, hi):
    return max(lo, min(hi, x))

def move(deg, rpm, kp=KP, ki=KI, kd=KD, kvp=KVP, kvi=KVI,
         acc=ACC, dec=DEC, int_zone=INT_ZONE, max_steps=200000):
    target = deg / 360.0 * PPR
    vmax   = rpm / 60.0 * PPR
    pos, rate = 0.0, 0.0
    i_term, vel_i, e_prev = 0.0, 0.0, target - pos
    hist = []
    for n in range(max_steps):
        err = target - pos
        dist = abs(err)
        sgn = 1.0 if err >= 0 else -1.0
        # ---- 位置式梯形速度规划(前馈) ----
        v = abs(rate)
        if dist <= 0.5:
            v_ff = 0.0
        elif dist <= (v*v) / (2.0*dec):
            v_ff = max(v - dec*DT, 0.0)
        elif v < vmax:
            v_ff = min(v + acc*DT, vmax)
        else:
            v_ff = vmax
        v_ff *= sgn
        # ---- 位置环 PID(接近区才积分,输出限幅) ----
        p = kp * err
        if abs(err) < int_zone:
            i_term = clip(i_term + ki*err*DT, -CORR_LIM, CORR_LIM)
        else:
            i_term = 0.0
        d = kd * (err - e_prev) / DT
        corr = clip(p + i_term + d, -CORR_LIM, CORR_LIM)
        v_cmd = clip(v_ff + corr, -vmax*1.1, vmax*1.1)
        # ---- 速度环 PI ----
        rate_err = v_cmd - rate
        vel_i = clip(vel_i + kvi*rate_err*DT, -VEL_I_LIM, VEL_I_LIM)
        rate = clip(v_cmd + kvp*rate_err + vel_i, -vmax*1.1, vmax*1.1)
        # ---- 脉冲域位置:到达目标立即停 ----
        step = rate * DT
        if abs(err) <= abs(step):
            pos = target
            rate = 0.0
            break
        pos += step
        e_prev = err
        hist.append((n*DT, pos, rate, v_ff))
    t = np.array([h[0] for h in hist])
    p_ = np.array([h[1] for h in hist])
    r_ = np.array([h[2] for h in hist])
    ff = np.array([h[3] for h in hist])
    # 到位前速率(越小停止越平稳)
    stop_rate = abs(rate)
    return dict(t=t, pos=p_, rate=r_, ff=ff, target=target, vmax=vmax,
                stop_rate=stop_rate, done=(pos == target))

def report(name, r):
    if not r['done']:
        print(f"{name:24s} 未收敛"); return
    rate = np.abs(r['rate'])
    cruise = rate[(np.abs(r['ff']) > 0.9*r['vmax']) & (rate > 0.9*r['vmax'])]
    ripple = (cruise.max()-cruise.min())/r['vmax']*100 if len(cruise) else 0
    print(f"{name:24s} t={r['t'][-1]*1000:7.1f}ms  匀速脉动={ripple:5.1f}%  "
          f"到位前速率={r['stop_rate']:6.1f}pps  终点={r['pos'][-1]:.0f}/{r['target']:.0f}步")

if __name__ == '__main__':
    print("== 默认参数 ==")
    report("100度@30rpm",  move(100, 30))
    report("90度@60rpm",   move(90, 60))
    report("22.5度@10rpm", move(22.5, 10))
    report("360度@120rpm", move(360, 120))
    report("720度@60rpm",  move(720, 60))
    print("\n== 位置环 Kp 敏感性 ==")
    report("100度@30rpm Kp=4",  move(100, 30, kp=4))
    report("100度@30rpm Kp=25", move(100, 30, kp=25))
    print("\n== 位置环 Kd 敏感性 ==")
    report("100度@30rpm Kd=0",  move(100, 30, kd=0.0))
    report("100度@30rpm Kd=0.3",move(100, 30, kd=0.3))
    print("\n== 速度环 Kvi 敏感性 ==")
    report("100度@30rpm Kvi=0",  move(100, 30, kvi=0.0))
    report("100度@30rpm Kvi=25", move(100, 30, kvi=25.0))
