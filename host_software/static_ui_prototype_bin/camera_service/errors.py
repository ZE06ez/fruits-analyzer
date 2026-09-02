from __future__ import annotations


class CameraError(RuntimeError):
    """Base class for camera-service errors with user and technical messages."""

    def __init__(self, user_message: str, technical_message: str = "") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.technical_message = technical_message or user_message

    def to_dict(self) -> dict[str, str]:
        return {
            "userMessage": self.user_message,
            "technicalMessage": self.technical_message,
        }


class CameraUnavailableError(CameraError):
    """Camera device or runtime is unavailable."""


class CameraOpenError(CameraError):
    """Camera could not be opened."""


class CameraCaptureError(CameraError):
    """Camera failed while capturing a frame."""


class CameraTimeoutError(CameraCaptureError):
    """Camera capture timed out."""


class CameraSettingUnsupported(CameraError):
    """Camera setting is unsupported by the current adapter or driver."""


class CameraSdkUnavailableError(CameraUnavailableError):
    """Vendor SDK files are missing or not usable."""
