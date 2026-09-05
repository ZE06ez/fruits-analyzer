#ifndef __CTRL_DOOR_H
#define __CTRL_DOOR_H

#include <stdint.h>

/* 控制层:升降门(推杆)状态机,非阻塞
   命令:0=升起(缩回) 1=关闭(伸出) 2=立即停止
   安全:换向必须先刹车 ≥100ms;运动超时自动停机(防卡死/撞机) */

#define DOOR_CMD_OPEN    0u   /* 升起门:推杆缩回 */
#define DOOR_CMD_CLOSE   1u   /* 关闭门:推杆伸出 */
#define DOOR_CMD_STOP    2u   /* 立即停止(刹车) */
#define DOOR_REVERSE_MS  100u /* 换向刹车死区(文档要求 ≥100ms) */
#define DOOR_TIMEOUT_MS  28000u /* 单次动作超时自动停机(实测全行程 24s + 4s 余量) */

/* 方向定义(首次点动实测,反了交换下面两行):
   关闭=伸出:IN1=1,IN2=0 */
#define DOOR_CLOSE_IN1   1u
#define DOOR_CLOSE_IN2   0u
/* 升起=缩回:IN1=0,IN2=1 */
#define DOOR_OPEN_IN1    0u
#define DOOR_OPEN_IN2    1u

/* 状态:0=停止 1=关闭中 2=升起中 3=换向刹车中 */
#define DOOR_ST_STOP     0u
#define DOOR_ST_CLOSING  1u
#define DOOR_ST_OPENING  2u
#define DOOR_ST_REVERSING 3u

void    ctrl_door_init(void);
void    ctrl_door_poll(void);                 /* 主循环调用:推进状态机 */
uint8_t ctrl_door_command(uint8_t cmd);       /* 返回 0=接受 1=非法命令 */
uint8_t ctrl_door_state(void);                /* 当前状态 */

#endif
