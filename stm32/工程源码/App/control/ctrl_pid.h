#ifndef __CTRL_PID_H
#define __CTRL_PID_H

/* 通用增量式 PID(浮点,抗积分饱和:积分与输出均限幅到 out_lim) */

typedef struct
{
  float kp, ki, kd;      /* 增益(KD=0 即为 PI) */
  float dt;              /* 控制周期 s */
  float out_lim;         /* 输出/积分限幅 */
  float i_term;          /* 积分累计 */
  float prev_err;        /* 上次误差 */
} pid_t;

void  pid_init(pid_t *p, float kp, float ki, float kd, float dt, float out_lim);
void  pid_reset(pid_t *p);
float pid_update(pid_t *p, float setpoint, float measurement);

#endif
