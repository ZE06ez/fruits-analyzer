from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable


LOGGER = logging.getLogger(__name__)


class SerialServiceError(RuntimeError):
    """串口服务基础异常。"""


class SerialDependencyError(SerialServiceError):
    """缺少 pyserial。"""


class SerialConnectionError(SerialServiceError):
    """串口连接或通信失败。"""


class SerialNotConnectedError(SerialServiceError):
    """尚未连接 STM32。"""


class SerialResponseTimeout(SerialServiceError):
    """等待 STM32 回复超时。"""


class ProtocolResponseError(SerialServiceError):
    """STM32 回复不符合两字节协议。"""


@dataclass(frozen=True)
class SerialPortInfo:
    device: str
    description: str = ""
    hwid: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _default_serial_factory(**kwargs):
    try:
        import serial
    except ImportError as exc:
        raise SerialDependencyError(
            "缺少 pyserial，请执行：pip install pyserial==3.5"
        ) from exc

    return serial.Serial(**kwargs)


def _default_ports_provider() -> Iterable[Any]:
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise SerialDependencyError(
            "缺少 pyserial，请执行：pip install pyserial==3.5"
        ) from exc

    return list_ports.comports()


class SerialService:
    """
    STM32F407 两字节串口通讯服务。

    电脑发送：
        [CMD][PARAM]

    STM32 回复：
        [CMD | 0x80][RESULT]

    严格遵守一问一答，同一时间只允许一条命令执行。
    本服务不会自动重发命令，避免机械动作重复执行。
    """

    BAUDRATE = 115200
    BYTESIZE = 8
    PARITY = "N"
    STOPBITS = 1

    PING_CMD = 0x01
    PING_VALUE = 0x5A

    def __init__(
        self,
        *,
        serial_factory: Callable[..., Any] | None = None,
        ports_provider: Callable[[], Iterable[Any]] | None = None,
        default_timeout_s: float = 0.5,
    ) -> None:
        if default_timeout_s <= 0:
            raise ValueError("default_timeout_s 必须大于 0")

        self._serial_factory = serial_factory or _default_serial_factory
        self._ports_provider = ports_provider or _default_ports_provider
        self._default_timeout_s = float(default_timeout_s)

        self._serial: Any | None = None
        self._port_name = ""

        # 使用可重入锁，保证连接、断开和命令收发不会互相冲突。
        self._lock = threading.RLock()

    @property
    def is_connected(self) -> bool:
        port = self._serial
        return bool(port is not None and getattr(port, "is_open", False))

    @property
    def port_name(self) -> str:
        return self._port_name if self.is_connected else ""

    def list_ports(self) -> list[SerialPortInfo]:
        """返回 Windows 当前可用的串口列表。"""

        try:
            raw_ports = self._ports_provider()
            ports = [
                SerialPortInfo(
                    device=str(getattr(item, "device", "")),
                    description=str(getattr(item, "description", "")),
                    hwid=str(getattr(item, "hwid", "")),
                )
                for item in raw_ports
                if str(getattr(item, "device", "")).strip()
            ]
        except SerialServiceError:
            raise
        except Exception as exc:
            raise SerialConnectionError(f"读取串口列表失败：{exc}") from exc

        return sorted(ports, key=lambda item: item.device.upper())

    def connect(self, port: str) -> None:
        """以 115200、8N1 连接 STM32F407。"""

        port = str(port).strip()
        if not port:
            raise ValueError("串口名称不能为空")

        with self._lock:
            self.disconnect()

            try:
                serial_port = self._serial_factory(
                    port=port,
                    baudrate=self.BAUDRATE,
                    bytesize=self.BYTESIZE,
                    parity=self.PARITY,
                    stopbits=self.STOPBITS,
                    timeout=self._default_timeout_s,
                    write_timeout=self._default_timeout_s,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False,
                )

                if not getattr(serial_port, "is_open", False):
                    open_method = getattr(serial_port, "open", None)
                    if not callable(open_method):
                        raise SerialConnectionError(
                            f"串口 {port} 未打开，且对象不支持 open()"
                        )
                    open_method()

                self._serial = serial_port
                self._port_name = port
                self._clear_buffers(serial_port)

                LOGGER.info(
                    "STM32 串口已连接：%s，115200 8N1",
                    port,
                )

            except SerialServiceError:
                self._serial = None
                self._port_name = ""
                raise
            except Exception as exc:
                self._serial = None
                self._port_name = ""
                raise SerialConnectionError(
                    f"连接串口 {port} 失败：{exc}"
                ) from exc

    def disconnect(self) -> None:
        """关闭当前串口连接。"""

        with self._lock:
            serial_port = self._serial
            previous_port = self._port_name

            self._serial = None
            self._port_name = ""

            if serial_port is None:
                return

            try:
                if getattr(serial_port, "is_open", False):
                    serial_port.close()
            except Exception as exc:
                LOGGER.warning("关闭串口 %s 时发生异常：%s", previous_port, exc)

    def ping(self, timeout_s: float = 0.5) -> bool:
        """
        按协议执行 PING。

        TX: 01 5A
        RX: 81 5A
        """

        result = self.send_command(
            self.PING_CMD,
            self.PING_VALUE,
            timeout_s=timeout_s,
        )

        if result != self.PING_VALUE:
            raise ProtocolResponseError(
                "PING 返回值错误："
                f"期望 0x{self.PING_VALUE:02X}，"
                f"实际 0x{result:02X}"
            )

        return True

    def send_command(
        self,
        cmd: int,
        param: int,
        timeout_s: float | None = None,
    ) -> int:
        """
        发送一条两字节命令并返回 RESULT。

        不会自动重试。机械命令超时后，应由上层查询状态或重新寻零。
        """

        self._validate_byte("cmd", cmd)
        self._validate_byte("param", param)

        if not 0x01 <= cmd <= 0x7F:
            raise ValueError("电脑命令 CMD 必须在 0x01～0x7F 范围内")

        actual_timeout = (
            self._default_timeout_s
            if timeout_s is None
            else float(timeout_s)
        )

        if actual_timeout <= 0:
            raise ValueError("timeout_s 必须大于 0")

        with self._lock:
            serial_port = self._require_connection()
            request = bytes((cmd, param))
            expected_reply_cmd = cmd | 0x80

            try:
                # 两字节协议没有帧头和 CRC，因此每条新命令前清理旧数据。
                reset_input = getattr(serial_port, "reset_input_buffer", None)
                if callable(reset_input):
                    reset_input()

                written = serial_port.write(request)
                if written != 2:
                    raise SerialConnectionError(
                        f"串口写入不完整：期望 2 字节，实际 {written} 字节"
                    )

                flush = getattr(serial_port, "flush", None)
                if callable(flush):
                    flush()

                LOGGER.debug(
                    "串口 TX：%02X %02X",
                    cmd,
                    param,
                )

                reply = self._read_exactly(
                    serial_port,
                    size=2,
                    timeout_s=actual_timeout,
                )

            except SerialServiceError:
                raise
            except Exception as exc:
                raise SerialConnectionError(
                    f"串口通信失败：{exc}"
                ) from exc

            reply_cmd = reply[0]
            result = reply[1]

            LOGGER.debug(
                "串口 RX：%02X %02X",
                reply_cmd,
                result,
            )

            if reply_cmd != expected_reply_cmd:
                raise ProtocolResponseError(
                    "回复命令不匹配："
                    f"发送 0x{cmd:02X}，"
                    f"期望回复 0x{expected_reply_cmd:02X}，"
                    f"实际收到 0x{reply_cmd:02X}"
                )

            return result

    def _read_exactly(
        self,
        serial_port: Any,
        *,
        size: int,
        timeout_s: float,
    ) -> bytes:
        deadline = time.monotonic() + timeout_s
        received = bytearray()

        original_timeout = getattr(serial_port, "timeout", None)

        try:
            while len(received) < size:
                remaining = deadline - time.monotonic()

                if remaining <= 0:
                    received_hex = received.hex(" ").upper() or "无"
                    raise SerialResponseTimeout(
                        f"等待 STM32 回复超时，已收到：{received_hex}"
                    )

                if hasattr(serial_port, "timeout"):
                    serial_port.timeout = min(remaining, 0.05)

                chunk = serial_port.read(size - len(received))

                if chunk:
                    received.extend(chunk)

            return bytes(received)

        finally:
            if hasattr(serial_port, "timeout"):
                serial_port.timeout = original_timeout

    def _require_connection(self) -> Any:
        if not self.is_connected:
            raise SerialNotConnectedError("尚未连接 STM32 串口")

        return self._serial

    @staticmethod
    def _clear_buffers(serial_port: Any) -> None:
        reset_input = getattr(serial_port, "reset_input_buffer", None)
        reset_output = getattr(serial_port, "reset_output_buffer", None)

        if callable(reset_input):
            reset_input()

        if callable(reset_output):
            reset_output()

    @staticmethod
    def _validate_byte(name: str, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} 必须是整数")

        if not 0 <= value <= 0xFF:
            raise ValueError(f"{name} 必须在 0x00～0xFF 范围内")

    def __enter__(self) -> "SerialService":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.disconnect()
