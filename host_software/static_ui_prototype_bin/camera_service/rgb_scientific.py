from __future__ import annotations

from typing import Any


LOSSY_FOURCC = {"MJPG", "MJPE", "JPEG", "JPG", "H264", "H265", "X264", "X265", "AVC1", "HEVC"}
CHROMA_SUBSAMPLED_FOURCC = {"YUY2", "YUYV", "UYVY"}
STRICT_LOSSLESS_FOURCC = {
    "RGB3",
    "RGB ",
    "BGR3",
    "BGR ",
    "DIB ",
    "RAW ",
    "BA81",
    "BGGR",
    "GBRG",
    "GRBG",
    "RGGB",
}


def normalize_fourcc(value: Any) -> str:
    return str(value or "").strip().upper()[:4]


def classify_rgb_scientific_transport(
    *,
    requested_fourcc: Any = "",
    actual_fourcc: Any = "",
    color_space: str = "RGB",
    dtype: str = "uint8",
) -> dict[str, Any]:
    requested = normalize_fourcc(requested_fourcc)
    actual = normalize_fourcc(actual_fourcc)
    if not actual:
        return {
            "requestedFourcc": requested,
            "actualFourcc": "",
            "sourcePixelFormat": "unknown",
            "sourceCompression": "unknown",
            "scientificStrictLossless": False,
            "scientificCaptureApproved": False,
            "scientificQualityClass": "unknown",
            "chromaSubsampling": "unknown",
            "reason": "actual_transport_not_read",
            "outputFormat": "PNG",
            "outputLossless": True,
        }
    source_format = actual
    if source_format in LOSSY_FOURCC:
        compression = "lossy"
        strict = False
        approved = False
        quality_class = "lossy"
        chroma = "unknown"
        reason = "lossy_transport"
    elif source_format in CHROMA_SUBSAMPLED_FOURCC:
        compression = "uncompressed_but_chroma_subsampled"
        strict = False
        approved = True
        quality_class = "uncompressed_422"
        chroma = "4:2:2"
        reason = "chroma_subsampled_4_2_2"
    elif source_format in STRICT_LOSSLESS_FOURCC:
        compression = "none"
        strict = str(color_space or "").upper() == "RGB" and str(dtype or "").lower() == "uint8"
        approved = strict
        quality_class = "strict_lossless" if strict else "unknown"
        chroma = "none" if strict else "unknown"
        reason = "strict_lossless_verified" if strict else "rgb_frame_format_invalid"
    else:
        compression = "unknown"
        strict = False
        approved = False
        quality_class = "unknown"
        chroma = "unknown"
        reason = "transport_not_verified"
    return {
        "requestedFourcc": requested,
        "actualFourcc": actual,
        "sourcePixelFormat": source_format,
        "sourceCompression": compression,
        "scientificStrictLossless": strict,
        "scientificCaptureApproved": approved,
        "scientificQualityClass": quality_class,
        "chromaSubsampling": chroma,
        "reason": reason,
        "outputFormat": "PNG",
        "outputLossless": True,
    }
