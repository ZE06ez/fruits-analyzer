/* 控制层:上位机协议实现(二进制帧 + 短帧 + ASCII 文本,全部非阻塞) */
#include "ctrl_protocol.h"
#include "ctrl_motor.h"
#include "ctrl_door.h"
#include "../bsp/bsp_pwm.h"
#include "../driver/drv_uart.h"
#include <string.h>
#include <stdlib.h>

#define FRAME_MAX_PLEN  32u

/* ---------- 内部状态 ---------- */
static uint8_t  s_buf[FRAME_MAX_PLEN + 4];   /* CMD|PLEN|PAYLOAD */
static uint8_t  s_plen, s_idx;
static uint16_t s_crc;
static uint8_t  s_crc_rx_hi;
static uint8_t  s_stage;                     /* 二进制解析状态机 */
static uint32_t s_last_status_tick;

/* ASCII 文本状态 */
static char     s_line[64];                  /* 文本行缓冲 */
static uint8_t  s_line_len;
static uint8_t  s_boot_stage;                /* 0=检查电机状态 1=已ready */
static uint32_t s_last_check_tick;
static uint8_t  s_move_pending;              /* MOVE 执行中,完成回 MOVE_OK */
static uint8_t  s_frame_guard;               /* 二进制帧字节计数(防解析卡死) */
static uint8_t  s_short_cmd;                 /* 短帧协议:0=空闲,0x10/0x12=已收命令字节 */

/* ---------- CRC16-CCITT-FALSE ---------- */
static uint16_t crc16_byte(uint16_t crc, uint8_t b)
{
  crc ^= (uint16_t)b << 8;
  for (uint8_t i = 0; i < 8; i++)
    crc = (crc & 0x8000u) ? ((crc << 1) ^ 0x1021u) : (crc << 1);
  return crc;
}

/* ---------- 发送(非阻塞入队) ---------- */
static void proto_send(uint8_t cmd, uint8_t plen, const uint8_t *pl)
{
  uint8_t f[2u + 2u + FRAME_MAX_PLEN + 2u];
  uint16_t crc = 0xFFFFu;
  uint8_t n = 0;
  f[n++] = 0xAA; f[n++] = 0x55;
  f[n++] = cmd;      crc = crc16_byte(crc, cmd);
  f[n++] = plen;     crc = crc16_byte(crc, plen);
  for (uint8_t i = 0; i < plen; i++) { f[n++] = pl[i]; crc = crc16_byte(crc, pl[i]); }
  f[n++] = (uint8_t)(crc >> 8);
  f[n++] = (uint8_t)(crc & 0xFF);
  drv_uart_send(f, n);
}

static void proto_ack(uint8_t echo, uint8_t result)
{
  uint8_t p[2] = { echo, result };
  proto_send(RSP_ACK, 2u, p);
}

static void proto_status(void)
{
  uint8_t p[18];
  float f;
  p[0] = ctrl_motor_state();
  p[1] = 0u;                                  /* err 预留 */
  f = ctrl_motor_pos_deg();    memcpy(&p[2],  &f, 4);
  f = ctrl_motor_vel_rpm();    memcpy(&p[6],  &f, 4);
  f = ctrl_motor_target_deg(); memcpy(&p[10], &f, 4);
  p[14] = bsp_pwm_get_duty(1u);               /* 风扇占空比% */
  p[15] = bsp_pwm_get_duty(2u);               /* LED1 */
  p[16] = bsp_pwm_get_duty(3u);               /* LED2 */
  p[17] = bsp_pwm_get_duty(4u);               /* LED3 */
  proto_send(RSP_STATUS, 18u, p);
}

void ctrl_protocol_send_info(void)
{
  motor_cfg_t *c = ctrl_motor_cfg();
  uint8_t p[13];
  float f;
  p[0] = FW_VERSION;
  f = c->ppr;       memcpy(&p[1],  &f, 4);
  f = c->max_rpm;   memcpy(&p[5],  &f, 4);
  f = c->acc;       memcpy(&p[9],  &f, 4);
  proto_send(RSP_INFO, 13u, p);
}

/* ---------- 命令分发 ---------- */
static void handle_frame(uint8_t cmd, uint8_t plen, uint8_t *pl)
{
  motor_cfg_t *c = ctrl_motor_cfg();
  float a, b, c2;

  switch (cmd)
  {
  case CMD_MOVE_ABS:
    if (plen != 8u) { proto_ack(cmd, RES_BAD_PARAM); return; }
    memcpy(&a, &pl[0], 4);  memcpy(&b, &pl[4], 4);
    if (a < -100000.0f || a > 100000.0f || b < 0.0f || b > MOTOR_RPM_HARD_LIMIT + 1.0f)
    { proto_ack(cmd, RES_BAD_PARAM); return; }
    proto_ack(cmd, ctrl_motor_move_abs(a, b) ? RES_BUSY : RES_OK);
    break;

  case CMD_MOVE_REL:
    if (plen != 8u) { proto_ack(cmd, RES_BAD_PARAM); return; }
    memcpy(&a, &pl[0], 4);  memcpy(&b, &pl[4], 4);
    if (a < -100000.0f || a > 100000.0f || b < 0.0f || b > MOTOR_RPM_HARD_LIMIT + 1.0f)
    { proto_ack(cmd, RES_BAD_PARAM); return; }
    proto_ack(cmd, ctrl_motor_move_rel(a, b) ? RES_BUSY : RES_OK);
    break;

  case CMD_STOP:
    if (plen != 0u) { proto_ack(cmd, RES_BAD_PARAM); return; }
    ctrl_motor_stop();
    proto_ack(cmd, RES_OK);
    break;

  case CMD_SET_POS_PID:
    if (plen != 12u) { proto_ack(cmd, RES_BAD_PARAM); return; }
    memcpy(&a, &pl[0], 4);  memcpy(&b, &pl[4], 4);  memcpy(&c2, &pl[8], 4);
    if (a < 0.0f || a > 100.0f || b < 0.0f || b > 10.0f || c2 < 0.0f || c2 > 10.0f)
    { proto_ack(cmd, RES_BAD_PARAM); return; }
    c->pos_kp = a; c->pos_ki = b; c->pos_kd = c2;
    ctrl_motor_apply_cfg();
    proto_ack(cmd, RES_OK);
    break;

  case CMD_SET_VEL_PID:
    if (plen != 8u) { proto_ack(cmd, RES_BAD_PARAM); return; }
    memcpy(&a, &pl[0], 4);  memcpy(&b, &pl[4], 4);
    if (a < 0.0f || a > 10.0f || b < 0.0f || b > 100.0f)
    { proto_ack(cmd, RES_BAD_PARAM); return; }
    c->vel_kp = a; c->vel_ki = b;
    ctrl_motor_apply_cfg();
    proto_ack(cmd, RES_OK);
    break;

  case CMD_SET_PROFILE:
    if (plen != 12u) { proto_ack(cmd, RES_BAD_PARAM); return; }
    memcpy(&a, &pl[0], 4);  memcpy(&b, &pl[4], 4);  memcpy(&c2, &pl[8], 4);
    if (a < 1.0f || a > MOTOR_RPM_HARD_LIMIT || b < 100.0f || b > 100000.0f ||
        c2 < 100.0f || c2 > 100000.0f)
    { proto_ack(cmd, RES_BAD_PARAM); return; }
    c->max_rpm = a; c->acc = b; c->dec = c2;
    proto_ack(cmd, RES_OK);
    break;

  case CMD_SET_CONFIG:
    if (plen != 4u) { proto_ack(cmd, RES_BAD_PARAM); return; }
    memcpy(&a, &pl[0], 4);
    if (a < 200.0f || a > 60000.0f)
    { proto_ack(cmd, RES_BAD_PARAM); return; }
    c->ppr = a;
    ctrl_motor_apply_cfg();     /* 同步 500rpm 硬限幅对应 pps */
    proto_ack(cmd, RES_OK);
    break;

  case CMD_QUERY_STATUS:
    if (plen != 0u) { proto_ack(cmd, RES_BAD_PARAM); return; }
    proto_status();
    break;

  case CMD_SET_ORIGIN:
    if (plen != 0u) { proto_ack(cmd, RES_BAD_PARAM); return; }
    ctrl_motor_set_origin();
    proto_ack(cmd, RES_OK);
    break;

  case CMD_RESET:
    if (plen != 0u) { proto_ack(cmd, RES_BAD_PARAM); return; }
    proto_ack(cmd, RES_OK);
    NVIC_SystemReset();
    break;

  case CMD_FAN_SET:
    if (plen != 1u || pl[0] > 100u) { proto_ack(cmd, RES_BAD_PARAM); return; }
    bsp_pwm_set_duty(1u, pl[0]);       /* 风扇:0=关,100=满速 */
    proto_ack(cmd, RES_OK);
    break;

  case CMD_DOOR_SET:
    if (plen != 1u || pl[0] > 2u) { proto_ack(cmd, RES_BAD_PARAM); return; }
    proto_ack(cmd, ctrl_door_command(pl[0]) ? RES_BAD_PARAM : RES_OK);
    break;

  case CMD_LED_SET:
    if (plen != 1u || pl[0] > 0x07u) { proto_ack(cmd, RES_BAD_PARAM); return; }
    bsp_pwm_set_duty(2u, (pl[0] & 0x01u) ? 100u : 0u);   /* LED1 */
    bsp_pwm_set_duty(3u, (pl[0] & 0x02u) ? 100u : 0u);   /* LED2 */
    bsp_pwm_set_duty(4u, (pl[0] & 0x04u) ? 100u : 0u);   /* LED3 */
    proto_ack(cmd, RES_OK);
    break;

  default:
    proto_ack(cmd, RES_BAD_CMD);
    break;
  }
}

void ctrl_protocol_init(void)
{
  s_stage = 0u;
  s_last_status_tick = 0u;
  s_line_len = 0u;
  s_boot_stage = 0u;
  s_last_check_tick = 0u;
  s_move_pending = 0u;
}

/* ---------- ASCII 文本命令(人工验证) ---------- */
static void text_send(const char *s)
{
  drv_uart_send((const uint8_t *)s, (uint16_t)strlen(s));
}

static void ascii_cmd_en(void)
{
  ctrl_motor_enable();
  text_send("EN_OK\r\n");
}

/* 手动解析浮点(避开 newlib-nano scanf 不支持 %f 的限制);成功返回0 */
static int parse_float(char **pp, float *out)
{
  while (**pp == ' ' || **pp == '\t') (*pp)++;
  char *end;
  *out = strtof(*pp, &end);
  if (end == *pp) return -1;          /* 无有效数字 */
  *pp = end;
  return 0;
}

/* 手动解析无符号整数;成功返回0 */
static int parse_uint(char **pp, uint32_t *out)
{
  while (**pp == ' ' || **pp == '\t') (*pp)++;
  char *end;
  *out = strtoul(*pp, &end, 10);
  if (end == *pp) return -1;          /* 无有效数字 */
  *pp = end;
  return 0;
}

static void ascii_cmd_move(char *p)          /* p = "MOVE ..." 之后的部分 */
{
  float deg, rpm;
  if (parse_float(&p, &deg) || parse_float(&p, &rpm) ||
      deg < -100000.0f || deg > 100000.0f ||
      rpm < 0.0f || rpm > MOTOR_RPM_HARD_LIMIT + 1.0f)
  { text_send("MOVE_ERR\r\n"); return; }
  if (!ctrl_motor_is_enabled())             /* 必须先 EN */
  { text_send("MOVE_ERR\r\n"); return; }
  if (ctrl_motor_move_rel(deg, rpm) != 0)   /* 忙 */
  { text_send("MOVE_ERR\r\n"); return; }
  s_move_pending = 1u;
}

static void ascii_cmd_stop(void)
{
  ctrl_motor_stop();
  text_send("STOP_OK\r\n");
}

static void ascii_cmd_fan(char *p)          /* p = "FAN ..." 之后的部分 */
{
  uint32_t pct;
  if (parse_uint(&p, &pct) || pct > 100u)
  { text_send("FAN_ERR\r\n"); return; }
  bsp_pwm_set_duty(1u, (uint8_t)pct);      /* 0=关,100=满速 */
  text_send("FAN_OK\r\n");
}

static void ascii_cmd_led(char *p)          /* p = "LED ..." 之后的部分:LED <掩码0-7> */
{
  uint32_t mask;
  if (parse_uint(&p, &mask) || mask > 0x07u)
  { text_send("LED_ERR\r\n"); return; }
  bsp_pwm_set_duty(2u, (mask & 0x01u) ? 100u : 0u);   /* LED1 */
  bsp_pwm_set_duty(3u, (mask & 0x02u) ? 100u : 0u);   /* LED2 */
  bsp_pwm_set_duty(4u, (mask & 0x04u) ? 100u : 0u);   /* LED3 */
  text_send("LED_OK\r\n");
}

static void ascii_cmd_door(char *p)         /* p = "DOOR ..." 之后的部分:DOOR <0升起|1关闭|2停止> */
{
  uint32_t cmd;
  if (parse_uint(&p, &cmd) || cmd > 2u || ctrl_door_command((uint8_t)cmd) != 0u)
  { text_send("DOOR_ERR\r\n"); return; }
  text_send("DOOR_OK\r\n");
}

static void ascii_process_line(void)
{
  char *p = s_line;
  while (*p == ' ' || *p == '\t') p++;
  if (strncmp(p, "EN", 2) == 0 &&
      (p[2] == ' ' || p[2] == '\t' || p[2] == '\0'))
    ascii_cmd_en();
  else if (strncmp(p, "MOVE", 4) == 0)
    ascii_cmd_move(p + 4);
  else if (strncmp(p, "FAN", 3) == 0)
    ascii_cmd_fan(p + 3);
  else if (strncmp(p, "LED", 3) == 0)
    ascii_cmd_led(p + 3);
  else if (strncmp(p, "DOOR", 4) == 0)
    ascii_cmd_door(p + 4);
  else if (strncmp(p, "STOP", 4) == 0)
    ascii_cmd_stop();
  /* 其他文本行:忽略 */
}

/* ---------- 逐字节解析(二进制状态机 + ASCII 文本并行) ---------- */
void ctrl_protocol_rx(uint8_t b)
{
  /* 预判本字节是否属于二进制帧(帧头 AA/55 或帧内),属于则不进文本缓冲 */
  uint8_t in_bin = (s_stage >= 2u) ||
                   (s_stage == 0u && b == 0xAA) ||
                   (s_stage == 1u && b == 0x55);

  /* ① 二进制帧解析 */
  switch (s_stage)
  {
  case 0:  s_stage = (b == 0xAA) ? 1u : 0u;                        break;
  case 1:  s_stage = (b == 0x55) ? 2u : ((b == 0xAA) ? 1u : 0u);  break;
  case 2:  s_buf[0] = b; s_crc = crc16_byte(0xFFFFu, b); s_stage = 3u; break;
  case 3:
    if (b > FRAME_MAX_PLEN) { s_stage = 0u; break; }
    s_buf[1] = b;
    s_crc = crc16_byte(s_crc, b);
    s_plen = b; s_idx = 0u;
    s_stage = (s_plen > 0u) ? 4u : 5u;
    break;
  case 4:
    s_buf[2 + s_idx] = b;
    s_crc = crc16_byte(s_crc, b);
    if (++s_idx >= s_plen) s_stage = 5u;
    break;
  case 5:  s_crc_rx_hi = b; s_stage = 6u; break;
  case 6:
    s_stage = 0u;
    if ((((uint16_t)s_crc_rx_hi << 8) | b) == s_crc)
      handle_frame(s_buf[0], s_plen, &s_buf[2]);
    else
      proto_ack(s_buf[0], RES_CRC);
    break;
  default: s_stage = 0u; break;
  }

  /* ③ 短帧协议(接线文档):
       10 00 关风扇 / 10 01 开风扇(满速) → 回 90 00;10 02~64 = 占空比%(扩展)
       11 00 升起门 / 11 01 关闭门 / 11 02 停止推杆 → 回 91 00
       12 XX 三路LED位掩码(bit0=LED1 bit1=LED2 bit2=LED3)→ 回 92 00 */
  uint8_t short_consumed = 0;
  if (!in_bin)
  {
    if (s_short_cmd == 0u)
    {
      if (b == 0x10u || b == 0x11u || b == 0x12u) { s_short_cmd = b; short_consumed = 1; }
    }
    else if (s_short_cmd == 0x10u)   /* 风扇 */
    {
      s_short_cmd = 0u;
      short_consumed = 1;
      if (b <= 0x64u)
      {
        bsp_pwm_set_duty(1u, (b == 0x00u) ? 0u : ((b == 0x01u) ? 100u : b));
        uint8_t ack2[2] = { 0x90u, 0x00u };
        drv_uart_send(ack2, 2u);
      }
    }
    else if (s_short_cmd == 0x11u)   /* 推杆/升降门 */
    {
      s_short_cmd = 0u;
      short_consumed = 1;
      if (b <= 0x02u)
      {
        uint8_t ack2[2] = { 0x91u, 0x00u };
        if (ctrl_door_command(b) != 0u) ack2[1] = 0x02u;   /* 非法参数 */
        drv_uart_send(ack2, 2u);
      }
    }
    else                             /* 0x12:LED 位掩码 */
    {
      s_short_cmd = 0u;
      short_consumed = 1;
      if (b <= 0x07u)
      {
        bsp_pwm_set_duty(2u, (b & 0x01u) ? 100u : 0u);   /* LED1 */
        bsp_pwm_set_duty(3u, (b & 0x02u) ? 100u : 0u);   /* LED2 */
        bsp_pwm_set_duty(4u, (b & 0x04u) ? 100u : 0u);   /* LED3 */
        uint8_t ack2[2] = { 0x92u, 0x00u };
        drv_uart_send(ack2, 2u);
      }
    }
  }

  /* ② ASCII 文本行解析:仅累积不属于二进制帧/短帧的字节 */
  if (!in_bin && !short_consumed)
  {
    s_frame_guard = 0u;
    if (s_line_len < sizeof(s_line) - 1u)
    {
      if (b == '\r' || b == '\n')
      {
        if (s_line_len > 0u)
        {
          s_line[s_line_len] = '\0';
          ascii_process_line();
        }
        s_line_len = 0u;
      }
      else
      {
        s_line[s_line_len++] = (char)b;
      }
    }
    else
    {
      s_line_len = 0u;   /* 超长:丢弃该行 */
    }
  }
  else if (in_bin)
  {
    /* 二进制帧进行中:字节计数防解析卡死(残帧超过上限强制复位) */
    if (++s_frame_guard > 64u)
    {
      s_stage = 0u;
      s_line_len = 0u;
      s_frame_guard = 0u;
    }
  }
}

void ctrl_protocol_poll(void)
{
  /* 排空 RX 环形缓冲 → 协议解析(解析在主循环,中断只负责收字节) */
  int b;
  while ((b = drv_uart_rx_pop()) >= 0)
    ctrl_protocol_rx((uint8_t)b);

  /* 上电电机状态检查:每 0.5s,正常后回传 ready */
  if (s_boot_stage == 0u)
  {
    if ((HAL_GetTick() - s_last_check_tick) >= BOOT_CHECK_MS)
    {
      s_last_check_tick = HAL_GetTick();
      if (ctrl_motor_self_check())
      {
        s_boot_stage = 1u;
        text_send("ready\r\n");
      }
    }
  }

  /* MOVE 执行完成 → 回传 MOVE_OK */
  if (s_move_pending)
  {
    uint8_t st = ctrl_motor_state();
    if (st != MOTOR_MOVING && st != MOTOR_STOPPING)
    {
      s_move_pending = 0u;
      text_send("MOVE_OK\r\n");
    }
  }

  /* 周期状态上报 */
  if ((HAL_GetTick() - s_last_status_tick) >= STATUS_PERIOD_MS)
  {
    s_last_status_tick = HAL_GetTick();
    proto_status();
  }
}
