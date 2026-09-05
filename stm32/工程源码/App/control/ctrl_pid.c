/* 控制层:通用 PID 控制器 */
#include "ctrl_pid.h"

void pid_init(pid_t *p, float kp, float ki, float kd, float dt, float out_lim)
{
  p->kp = kp; p->ki = ki; p->kd = kd;
  p->dt = dt; p->out_lim = out_lim;
  p->i_term = 0.0f; p->prev_err = 0.0f;
}

void pid_reset(pid_t *p)
{
  p->i_term = 0.0f;
  p->prev_err = 0.0f;
}

float pid_update(pid_t *p, float setpoint, float measurement)
{
  float err = setpoint - measurement;

  p->i_term += p->ki * err * p->dt;               /* 积分(限幅抗饱和) */
  if (p->i_term >  p->out_lim) p->i_term =  p->out_lim;
  if (p->i_term < -p->out_lim) p->i_term = -p->out_lim;

  float d_term = p->kd * (err - p->prev_err) / p->dt;
  p->prev_err = err;

  float out = p->kp * err + p->i_term + d_term;
  if (out >  p->out_lim) out =  p->out_lim;
  if (out < -p->out_lim) out = -p->out_lim;
  return out;
}
