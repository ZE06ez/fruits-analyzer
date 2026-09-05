#ifndef __CTRL_PROTOCOL_H
#define __CTRL_PROTOCOL_H

#include <stdint.h>

/* 控制层:上位机协议
   帧格式:AA 55 CMD PLEN [载荷] CRCH CRCL
   CRC16-CCITT-FALSE(0x1021/0xFFFF)覆盖 CMD..载荷,CRC 高字节在前,小端序
   解析:接收中断逐字节状态机(非阻塞);发送:驱动层非阻塞队列 */

#define CMD_MOVE_ABS     0x01u   /* PLEN=8: float deg, float rpm */
#define CMD_MOVE_REL     0x02u   /* PLEN=8: float deg, float rpm */
#define CMD_STOP         0x03u   /* PLEN=0 */
#define CMD_SET_POS_PID  0x04u   /* PLEN=12: float Kp,Ki,Kd */
#define CMD_SET_VEL_PID  0x05u   /* PLEN=8: float Kp,Ki */
#define CMD_SET_PROFILE  0x06u   /* PLEN=12: float max_rpm,acc,dec */
#define CMD_SET_CONFIG   0x07u   /* PLEN=4: float ppr */
#define CMD_QUERY_STATUS 0x08u   /* PLEN=0 */
#define CMD_SET_ORIGIN   0x09u   /* PLEN=0 */
#define CMD_RESET        0x0Fu   /* PLEN=0 */
#define CMD_FAN_SET      0x10u   /* PLEN=1: u8 占空比% 0=关 100=满速(风扇=ch1) */
#define CMD_DOOR_SET     0x11u   /* PLEN=1: u8 0=升起(缩回) 1=关闭(伸出) 2=停止 */
#define CMD_LED_SET      0x12u   /* PLEN=1: u8 位掩码 0~7 (bit0=LED1 bit1=LED2 bit2=LED3, 全开=0x07) */

#define RSP_ACK          0x80u   /* PLEN=2: u8 echo_cmd, u8 result */
#define RSP_STATUS       0x81u   /* PLEN=18: state,err,pos,vel,tgt,fan,led1,led2,led3 */
#define RSP_INFO         0x82u   /* PLEN=13: u8 ver,float ppr,float max_rpm,float acc */

#define RES_OK       0u   /* 命令已执行 */
#define RES_CRC      1u   /* CRC 校验失败 */
#define RES_BAD_CMD  2u   /* 未知命令 */
#define RES_BAD_PARAM 3u  /* 参数超范围 */
#define RES_BUSY     4u   /* 电机运动中 */

#define FW_VERSION       1u
#define STATUS_PERIOD_MS 100u   /* 状态帧自动上报周期 */

/* ==================== ASCII 文本协议(人工上位机验证用) ==================== */
/* 与二进制协议自动共存:字节同时喂两个解析器,各自校验互不干扰。
   格式:文本行,回车/换行结尾(如串口助手直接输入) */
/*  上电: 每 BOOT_CHECK_MS 检查一次电机状态,正常回传 "ready" */
/*  "EN"           → 电机使能保持当前角度,回 "EN_OK" */
/*  "MOVE deg rpm" → 相对移动 deg°,速度 rpm,执行完回 "MOVE_OK";失败回 "MOVE_ERR"
                      (未先 EN 或参数越界或电机忙均回 MOVE_ERR) */
/*  "STOP"         → 减速停止,回 "STOP_OK" */
#define BOOT_CHECK_MS   500u    /* 上电电机状态检查周期(建议 0.5s/次) */

void ctrl_protocol_init(void);     /* 注册接收回调 + 发送 INFO */
void ctrl_protocol_poll(void);     /* 主循环调用:周期状态上报 */
void ctrl_protocol_rx(uint8_t b);  /* 收字节回调(驱动层调用) */
void ctrl_protocol_send_info(void);

#endif
