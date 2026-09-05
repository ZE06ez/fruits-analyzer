/* 控制层:升降门状态机(XY-160D 第1路)
   非阻塞:换向先刹车等 REVERSE_MS,超时自动停机;命令/状态查询均立即返回 */
#include "ctrl_door.h"
#include "../bsp/bsp_door.h"
#include "main.h"

static uint8_t  s_state;           /* 当前状态(DOOR_ST_*) */
static uint8_t  s_target_dir;      /* 换向刹车中的目标方向(1=关 2=开) */
static uint32_t s_rev_tick;        /* 换向刹车开始时刻 */
static uint32_t s_run_tick;        /* 本次运动开始时刻 */

static void door_set_dir(uint8_t close)   /* 设方向并 ENA=1 */
{
  if (close) bsp_door_drive(DOOR_CLOSE_IN1, DOOR_CLOSE_IN2);
  else       bsp_door_drive(DOOR_OPEN_IN1,  DOOR_OPEN_IN2);
}

void ctrl_door_init(void)
{
  bsp_door_init();
  s_state      = DOOR_ST_STOP;
  s_target_dir = 0u;
  s_rev_tick   = 0u;
  s_run_tick   = 0u;
}

/* 命令:0=升起 1=关闭 2=停止;返回 0=接受 1=非法 */
uint8_t ctrl_door_command(uint8_t cmd)
{
  if (cmd == DOOR_CMD_STOP)
  {
    bsp_door_brake();
    s_state = DOOR_ST_STOP;
    return 0u;
  }

  uint8_t want_close = (cmd == DOOR_CMD_CLOSE);      /* 1=关(伸) 0=开(缩) */
  uint8_t want_state = want_close ? DOOR_ST_CLOSING : DOOR_ST_OPENING;

  if (s_state == want_state) return 0u;              /* 同方向,已在动,忽略 */

  /* 从停止启动:直接走;运动中换向:先刹车死区 */
  if (s_state == DOOR_ST_STOP)
  {
    door_set_dir(want_close);
    s_state   = want_state;
    s_run_tick = HAL_GetTick();
  }
  else if (s_state == DOOR_ST_REVERSING)
  {
    if (want_close == s_target_dir) return 0u;       /* 换向中且目标同向,保持 */
    s_target_dir = want_close;                        /* 换向中换目标:重新计时 */
    s_rev_tick   = HAL_GetTick();
  }
  else
  {
    bsp_door_brake();                                 /* 刹车,等死区 */
    s_state      = DOOR_ST_REVERSING;
    s_target_dir = want_close;
    s_rev_tick   = HAL_GetTick();
  }
  return 0u;
}

/* 主循环推进:换向死区到时→按目标方向启动;运动超时→自动刹车 */
void ctrl_door_poll(void)
{
  uint32_t now = HAL_GetTick();

  if (s_state == DOOR_ST_REVERSING)
  {
    if ((now - s_rev_tick) >= DOOR_REVERSE_MS)
    {
      door_set_dir(s_target_dir);
      s_state    = s_target_dir ? DOOR_ST_CLOSING : DOOR_ST_OPENING;
      s_run_tick = now;
    }
  }
  else if (s_state == DOOR_ST_CLOSING || s_state == DOOR_ST_OPENING)
  {
    if ((now - s_run_tick) >= DOOR_TIMEOUT_MS)       /* 超时安全停机 */
    {
      bsp_door_brake();
      s_state = DOOR_ST_STOP;
    }
  }
}

uint8_t ctrl_door_state(void)
{
  return s_state;
}
