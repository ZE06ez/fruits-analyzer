/* 控制层:电机运动控制
   架构:梯形速度规划(前馈) → 位置环PID(跟踪参考轨迹) → 速度环PI → drv_pulse 发脉冲
   到位:脉冲计数到达目标(驱动层 ISR 精确停止),本层同步状态机 */
#include "ctrl_motor.h"
#include "ctrl_pid.h"
#include "../driver/drv_pulse.h"
#include <math.h>

static motor_cfg_t s_cfg;
static uint8_t s_state;
static int32_t s_target_steps;
static int8_t  s_dir_sign;
static float   s_pos_ref;      /* 参考轨迹位置(浮点) */
static float   s_v_ff;         /* 前馈速率 pps */
static float   s_max_rpm_cmd;  /* 本次运动速度上限(来自命令,已限幅) */
static uint8_t s_enabled;      /* 使能标志(EN 命令置位) */
static pid_t   s_pos_pid, s_vel_pid;

void ctrl_motor_apply_cfg(void)
{
  /* 500rpm 硬限幅 → 驱动层最大脉冲速率(ppr 变化时同步) */
  drv_pulse_set_max_pps((uint32_t)(MOTOR_RPM_HARD_LIMIT / 60.0f * s_cfg.ppr));

  pid_init(&s_pos_pid, s_cfg.pos_kp, s_cfg.pos_ki, s_cfg.pos_kd, MOTOR_CTRL_DT, s_cfg.corr_lim);
  pid_init(&s_vel_pid, s_cfg.vel_kp, s_cfg.vel_ki, 0.0f,          MOTOR_CTRL_DT, s_cfg.vel_i_lim);
}

void ctrl_motor_init(void)
{
  s_cfg.ppr       = MOTOR_PPR_DEFAULT;
  s_cfg.max_rpm   = MOTOR_RPM_HARD_LIMIT;   /* 默认上限 = 硬限幅 500rpm */
  s_cfg.acc       = MOTOR_ACC_DEFAULT;
  s_cfg.dec       = MOTOR_DEC_DEFAULT;
  s_cfg.pos_kp    = MOTOR_POS_KP;
  s_cfg.pos_ki    = MOTOR_POS_KI;
  s_cfg.pos_kd    = MOTOR_POS_KD;
  s_cfg.vel_kp    = MOTOR_VEL_KP;
  s_cfg.vel_ki    = MOTOR_VEL_KI;
  s_cfg.corr_lim  = MOTOR_CORR_LIM;
  s_cfg.vel_i_lim = MOTOR_VEL_I_LIM;

  ctrl_motor_apply_cfg();

  s_state = MOTOR_IDLE;
  s_target_steps = 0;
  s_pos_ref = 0.0f;
  s_v_ff = 0.0f;
  s_max_rpm_cmd = MOTOR_RPM_HARD_LIMIT;
  s_enabled = 0;
}

/* 电机状态自检:MCU 侧运动子系统完整即视为正常。
   HBS57H 内部状态无反馈接口;如需检测驱动器报警,
   可把 ALM±(9/10脚)接到 GPIO 后在此引脚判断 */
int ctrl_motor_self_check(void)
{
  return 1;
}

void ctrl_motor_enable(void)
{
  s_enabled = 1;   /* 驱动器 ENA 悬空=默认使能,电机保持当前角度 */
}

uint8_t ctrl_motor_is_enabled(void) { return s_enabled; }

void ctrl_motor_tick_1khz(void)
{
  /* 减速停止 */
  if (s_state == MOTOR_STOPPING)
  {
    float r = drv_pulse_get_rate();
    if (r > 0.0f)      { r -= s_cfg.dec * MOTOR_CTRL_DT; if (r < 0.0f) r = 0.0f; }
    else if (r < 0.0f) { r += s_cfg.dec * MOTOR_CTRL_DT; if (r > 0.0f) r = 0.0f; }
    drv_pulse_set_rate(r);
    if (r == 0.0f) { s_state = MOTOR_IDLE; drv_pulse_stop(); }
    return;
  }

  if (s_state != MOTOR_MOVING) return;

  int32_t steps = drv_pulse_get_steps();
  float max_rate = s_max_rpm_cmd / 60.0f * s_cfg.ppr;
  float max_lim  = max_rate;                 /* 精确限幅,不给 PID 超速裕量 */

  /* 1) 参考轨迹:位置式梯形速度规划 */
  float err_ref = (float)s_target_steps - s_pos_ref;
  float dist = (err_ref >= 0.0f) ? err_ref : -err_ref;
  float sgn  = (err_ref >= 0.0f) ? 1.0f : -1.0f;
  float v    = (s_v_ff >= 0.0f) ? s_v_ff : -s_v_ff;

  if (dist <= 0.5f)
    s_v_ff = 0.0f;
  else if (dist <= (v * v) / (2.0f * s_cfg.dec))
    s_v_ff = v - s_cfg.dec * MOTOR_CTRL_DT;      /* 减速段 */
  else if (v < max_rate)
    s_v_ff = v + s_cfg.acc * MOTOR_CTRL_DT;      /* 加速段 */
  else
    s_v_ff = max_rate;                           /* 匀速段 */

  if (s_v_ff < 0.0f) s_v_ff = 0.0f;
  s_v_ff *= sgn;
  s_pos_ref += s_v_ff * MOTOR_CTRL_DT;

  /* 2) 位置环:跟踪参考轨迹(正常误差≈0) */
  float corr = pid_update(&s_pos_pid, s_pos_ref, (float)steps);

  /* 3) 速度命令 = 前馈 + 位置环修正 */
  float v_cmd = s_v_ff + corr;
  if (v_cmd >  max_lim) v_cmd =  max_lim;
  if (v_cmd < -max_lim) v_cmd = -max_lim;

  /* 4) 速度环 PI(反馈=已应用速率) */
  float prev  = drv_pulse_get_rate();
  float corr2 = pid_update(&s_vel_pid, v_cmd, prev);
  float rate  = v_cmd + corr2;
  if (rate >  max_lim) rate =  max_lim;
  if (rate < -max_lim) rate = -max_lim;

  /* 5) 到位(驱动层已精确停,这里同步状态);否则最小爬行保证最后脉冲发出 */
  if ((s_dir_sign > 0 && steps >= s_target_steps) ||
      (s_dir_sign < 0 && steps <= s_target_steps))
  {
    drv_pulse_set_rate(0.0f);
    drv_pulse_stop();
    s_state = MOTOR_DONE;
    return;
  }
  if (steps != s_target_steps && fabsf(rate) < 2.0f)
    rate = (float)s_dir_sign * 2.0f;             /* 最小爬行 2pps */

  drv_pulse_set_rate(rate);
}

int ctrl_motor_move_abs(float deg, float rpm)
{
  if (s_state == MOTOR_MOVING || s_state == MOTOR_STOPPING) return -1; /* busy */

  /* 速度限幅:500rpm 硬限幅 → 配置上限 */
  if (rpm > MOTOR_RPM_HARD_LIMIT) rpm = MOTOR_RPM_HARD_LIMIT;
  if (rpm > s_cfg.max_rpm) rpm = s_cfg.max_rpm;
  if (rpm < 0.5f) rpm = 0.5f;

  int32_t steps = drv_pulse_get_steps();
  int32_t target = (int32_t)lroundf(deg / 360.0f * s_cfg.ppr);
  if (target == steps) return 0;

  s_max_rpm_cmd = rpm;
  s_target_steps = target;
  s_dir_sign = (target > steps) ? 1 : -1;
  s_pos_ref = (float)steps;
  s_v_ff = 0.0f;
  pid_reset(&s_pos_pid);
  pid_reset(&s_vel_pid);
  s_state = MOTOR_MOVING;

  drv_pulse_set_rate(0.0f);
  drv_pulse_set_target(target);
  drv_pulse_start(s_dir_sign);
  return 0;
}

int ctrl_motor_move_rel(float deg, float rpm)
{
  float abs_deg = ((float)drv_pulse_get_steps() + deg / 360.0f * s_cfg.ppr)
                  / s_cfg.ppr * 360.0f;
  return ctrl_motor_move_abs(abs_deg, rpm);
}

void ctrl_motor_stop(void)
{
  if (s_state == MOTOR_MOVING) s_state = MOTOR_STOPPING;
}

void ctrl_motor_set_origin(void)
{
  s_target_steps = 0;
  s_pos_ref = 0.0f;
  s_v_ff = 0.0f;
}

uint8_t ctrl_motor_state(void)         { return s_state; }
float   ctrl_motor_pos_deg(void)       { return (float)drv_pulse_get_steps() / s_cfg.ppr * 360.0f; }
float   ctrl_motor_vel_rpm(void)       { return drv_pulse_get_rate() / s_cfg.ppr * 60.0f; }
float   ctrl_motor_target_deg(void)    { return (float)s_target_steps / s_cfg.ppr * 360.0f; }
motor_cfg_t *ctrl_motor_cfg(void)      { return &s_cfg; }
