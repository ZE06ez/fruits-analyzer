/* 驱动层:电机脉冲引擎(TIM2 更新中断翻转 STEP)
   注意:速率取整为 0 时,运动中=暂停而非停机,防止到位前卡死 */
#include "drv_pulse.h"
#include "../bsp/bsp_tim.h"
#include "../bsp/bsp_gpio.h"

#define PULSE_TIM_CLK  84000000UL   /* TIM2 时钟 = APB1(84MHz)×2 */

static volatile int32_t  s_steps;    /* 已发脉冲(带方向) */
static volatile float    s_rate_pps; /* 当前速率(控制环写,ISR 读) */
static volatile int32_t  s_target;   /* 到位停止目标 */
static volatile uint8_t  s_phase;    /* STEP 相位:0=低 1=高 */
static volatile uint8_t  s_running;  /* 引擎运行标志 */
static int8_t  s_dir;                /* +1正转/-1反转 */
static uint32_t s_max_pps;           /* 速率硬上限(500rpm) */

void drv_pulse_init(void)
{
  s_steps = 0; s_rate_pps = 0.0f; s_target = 0;
  s_phase = 0; s_running = 0; s_dir = 1;
  s_max_pps = 13333u;                /* 默认 500rpm@1600ppr,启动时由控制层校准 */
}

void drv_pulse_start(int8_t dir)
{
  s_dir = dir;
  bsp_dir_set((uint8_t)(dir > 0));   /* DIR 先就绪(控制环 1ms 后才发首脉冲,满足 5us) */
  s_phase = 0;
  bsp_step_set(0);
  __HAL_TIM_SET_AUTORELOAD(&htim_pulse, PULSE_TIM_CLK / 200u - 1u); /* 初始 100pps */
  __HAL_TIM_SET_COUNTER(&htim_pulse, 0u);
  __HAL_TIM_CLEAR_IT(&htim_pulse, TIM_IT_UPDATE);
  __HAL_TIM_ENABLE_IT(&htim_pulse, TIM_IT_UPDATE);
  __HAL_TIM_ENABLE(&htim_pulse);
  s_running = 1;
}

void drv_pulse_stop(void)
{
  s_phase = 0;
  bsp_step_set(0);
  s_running = 0;
  __HAL_TIM_DISABLE_IT(&htim_pulse, TIM_IT_UPDATE);
  __HAL_TIM_DISABLE(&htim_pulse);
}

void drv_pulse_set_rate(float pps)   { s_rate_pps = pps; }
float drv_pulse_get_rate(void)       { return s_rate_pps; }
void drv_pulse_set_target(int32_t t) { s_target = t; }
void drv_pulse_set_max_pps(uint32_t m){ s_max_pps = m; }
int32_t drv_pulse_get_steps(void)    { return (int32_t)s_steps; }

void drv_pulse_isr(void)
{
  int32_t r = (int32_t)s_rate_pps;

  /* 速率取整为 0 */
  if (r == 0)
  {
    s_phase = 0;
    bsp_step_set(0);
    if (s_running)   /* 运动中:暂停,定时器保持,恢复速率后自动续发 */
    {
      __HAL_TIM_SET_AUTORELOAD(&htim_pulse, PULSE_TIM_CLK / 200u - 1u); /* ~5ms */
      __HAL_TIM_SET_COUNTER(&htim_pulse, 0u);
      return;
    }
    drv_pulse_stop();                /* 已停:真正停机 */
    return;
  }

  if (s_phase)       /* 下降沿:一个完整脉冲结束 */
  {
    s_phase = 0;
    bsp_step_set(0);
    /* 到位:脉冲计数精确到达目标 */
    if (s_running &&
        ((s_dir > 0 && s_steps >= s_target) ||
         (s_dir < 0 && s_steps <= s_target)))
    {
      drv_pulse_set_rate(0.0f);
      drv_pulse_stop();
      return;
    }
  }
  else               /* 上升沿:发出一个脉冲 */
  {
    s_phase = 1;
    bsp_step_set(1);
    s_steps += s_dir;
  }

  /* 设定下一半周期;硬限幅兜底(500rpm) */
  uint32_t hz = (r > 0) ? (uint32_t)r : (uint32_t)(-r);
  if (hz > s_max_pps) hz = s_max_pps;
  uint32_t half = PULSE_TIM_CLK / (2u * hz);
  if (half < 2u) half = 2u;
  __HAL_TIM_SET_AUTORELOAD(&htim_pulse, half - 1u);
  __HAL_TIM_SET_COUNTER(&htim_pulse, 0u);
}
