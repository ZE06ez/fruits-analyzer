#ifndef __CTRL_MOTOR_H
#define __CTRL_MOTOR_H

#include <stdint.h>

/* ==================== 特殊定义值(明确标注) ==================== */
#define MOTOR_RPM_HARD_LIMIT 500.0f   /* 速度硬限幅(rpm):命令/配置均不可超过,三层防护 */
#define MOTOR_PPR_DEFAULT    1600.0f  /* 每转脉冲数:必须与驱动器面板 P-00 一致 */
/* 快速启停:加速 40000 / 减速 60000 pps/s
   500rpm 时减速尾段 1000°→333°、667ms→222ms;22.5°(100脉冲)单步约100ms
   若驱动器报 Er05(位置超差)或机械冲击大,用 SET_PROFILE 调低 */
#define MOTOR_ACC_DEFAULT    40000.0f /* 加速度(pps/s):快速起步 */
#define MOTOR_DEC_DEFAULT    60000.0f /* 减速度(pps/s):快速刹车 */
#define MOTOR_POS_KP         8.0f     /* 位置环:比例 */
#define MOTOR_POS_KI         0.2f     /* 位置环:积分 */
#define MOTOR_POS_KD         0.0f     /* 位置环:微分(0,阻尼由速度环提供) */
#define MOTOR_VEL_KP         0.8f     /* 速度环:比例 */
#define MOTOR_VEL_KI         8.0f     /* 速度环:积分 */
#define MOTOR_CORR_LIM       300.0f   /* 位置环输出限幅(pps) */
#define MOTOR_VEL_I_LIM      400.0f   /* 速度环输出限幅(pps) */
#define MOTOR_CTRL_DT        0.001f   /* 控制周期(s)=TIM3 1kHz */
/* ============================================================== */

/* 运动状态 */
#define MOTOR_IDLE     0u   /* 空闲 */
#define MOTOR_MOVING   1u   /* 运动(加速/匀速/减速) */
#define MOTOR_STOPPING 2u   /* 减速停止中 */
#define MOTOR_DONE     3u   /* 已到位,保持 */

/* 运行参数(串口可实时修改) */
typedef struct
{
  float ppr;            /* 每转脉冲数 */
  float max_rpm;        /* 速度上限 rpm */
  float acc, dec;       /* 加减速 pps/s */
  float pos_kp, pos_ki, pos_kd;
  float vel_kp, vel_ki;
  float corr_lim, vel_i_lim;
} motor_cfg_t;

void  ctrl_motor_init(void);
void  ctrl_motor_tick_1khz(void);        /* 1kHz 控制环(TIM3 中断) */
int   ctrl_motor_move_abs(float deg, float rpm);   /* 绝对角度;返回0=接受 */
int   ctrl_motor_move_rel(float deg, float rpm);   /* 相对角度 */
void  ctrl_motor_stop(void);             /* 减速停止 */
void  ctrl_motor_set_origin(void);       /* 当前位置清零 */
void  ctrl_motor_enable(void);           /* 电机使能:保持当前角度 */
uint8_t ctrl_motor_is_enabled(void);
int   ctrl_motor_self_check(void);       /* 电机状态自检:0=异常 1=正常 */
uint8_t ctrl_motor_state(void);
float ctrl_motor_pos_deg(void);
float ctrl_motor_vel_rpm(void);
float ctrl_motor_target_deg(void);
motor_cfg_t *ctrl_motor_cfg(void);
void  ctrl_motor_apply_cfg(void);        /* 配置/参数修改后生效 */

#endif
