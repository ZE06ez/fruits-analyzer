from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from quality_algorithm.registration import (
    CameraRegistrationEndpoint,
    CheckerboardTarget,
    RegistrationError,
    build_registration_profile,
    compute_reprojection_errors,
    detect_checkerboard_corners,
    load_image,
    reprojection_metrics,
    save_detected_corners_visualization,
    save_warp_visualizations,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline RGB-to-DVP2 planar homography calibration from checkerboard image pairs.",
    )
    parser.add_argument("--rgb-image", type=Path, help="RGB scientific checkerboard PNG/JPEG for the reference pair.")
    parser.add_argument("--ms-image", type=Path, help="DVP2 monochrome checkerboard PNG/JPEG for the reference pair.")
    parser.add_argument(
        "--pair",
        action="append",
        default=[],
        help="Additional or alternative pair as name=rgb_path,ms_path. First pair calibrates, remaining pairs validate.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("manual_registration_output"))
    parser.add_argument("--profile-out", type=Path, default=None, help="Registration profile output path. Defaults to output-dir/registration_profile.json.")
    parser.add_argument("--board-cols", type=int, default=9, help="Checkerboard inner corner columns.")
    parser.add_argument("--board-rows", type=int, default=6, help="Checkerboard inner corner rows.")
    parser.add_argument("--square-mm", type=float, default=20.0, help="Checkerboard square size in millimeters.")
    parser.add_argument("--reference-band-nm", type=int, default=560, help="DVP2 reference band wavelength used for the checkerboard image.")
    parser.add_argument("--rgb-stable-id", default="")
    parser.add_argument("--rgb-device-index", type=int, default=1)
    parser.add_argument("--rgb-width", type=int, default=1920)
    parser.add_argument("--rgb-height", type=int, default=1080)
    parser.add_argument("--ms-stable-id", default="")
    parser.add_argument("--ms-serial", default="")
    parser.add_argument("--ms-width", type=int, default=2048)
    parser.add_argument("--ms-height", type=int, default=1200)
    parser.add_argument("--ransac-threshold", type=float, default=3.0)
    args = parser.parse_args()

    try:
        pairs = _collect_pairs(args)
        if not pairs:
            parser.error("Provide --rgb-image and --ms-image, or at least one --pair name=rgb_path,ms_path")
        output_dir = args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        target = CheckerboardTarget(
            innerCornersCols=args.board_cols,
            innerCornersRows=args.board_rows,
            squareSizeMm=args.square_mm,
        )
        detected: list[dict[str, Any]] = []
        for index, pair in enumerate(pairs):
            rgb_image = load_image(pair["rgbPath"])
            ms_image = load_image(pair["msPath"])
            rgb_corners = detect_checkerboard_corners(
                rgb_image,
                inner_corners_cols=target.innerCornersCols,
                inner_corners_rows=target.innerCornersRows,
            )
            ms_corners = detect_checkerboard_corners(
                ms_image,
                inner_corners_cols=target.innerCornersCols,
                inner_corners_rows=target.innerCornersRows,
            )
            pair_dir = output_dir if index == 0 else output_dir / pair["name"]
            pair_dir.mkdir(parents=True, exist_ok=True)
            save_detected_corners_visualization(
                pair_dir / ("rgb_corners.png" if index == 0 else f"{pair['name']}_rgb_corners.png"),
                rgb_image,
                rgb_corners,
                inner_corners_cols=target.innerCornersCols,
                inner_corners_rows=target.innerCornersRows,
            )
            save_detected_corners_visualization(
                pair_dir / ("ms_corners.png" if index == 0 else f"{pair['name']}_ms_corners.png"),
                ms_image,
                ms_corners,
                inner_corners_cols=target.innerCornersCols,
                inner_corners_rows=target.innerCornersRows,
            )
            detected.append({
                **pair,
                "rgbImageShape": tuple(int(value) for value in rgb_image.shape[:2]),
                "msImageShape": tuple(int(value) for value in ms_image.shape[:2]),
                "rgbCorners": rgb_corners,
                "msCorners": ms_corners,
            })

        reference = detected[0]
        profile, inliers = build_registration_profile(
            rgb_points=reference["rgbCorners"],
            multispectral_points=reference["msCorners"],
            rgb=CameraRegistrationEndpoint(
                stableId=args.rgb_stable_id,
                deviceIndex=args.rgb_device_index,
                width=args.rgb_width,
                height=args.rgb_height,
            ),
            multispectral=CameraRegistrationEndpoint(
                stableId=args.ms_stable_id,
                serial=args.ms_serial,
                width=args.ms_width,
                height=args.ms_height,
            ),
            target=target,
            reference_band_nm=args.reference_band_nm,
            source_pairs=[_pair_metadata(item, include_reference=(idx == 0)) for idx, item in enumerate(detected)],
            ransac_reproj_threshold=args.ransac_threshold,
        )
        visual_paths = save_warp_visualizations(
            rgb_image=load_image(reference["rgbPath"]),
            multispectral_image=load_image(reference["msPath"]),
            profile=profile,
            output_dir=output_dir,
        )
        profile_path = args.profile_out or output_dir / "registration_profile.json"
        profile.save_json(profile_path)
        diagnostics = {
            "status": "ok",
            "profilePath": str(profile_path),
            "method": profile.method,
            "referencePair": reference["name"],
            "referenceBandNm": args.reference_band_nm,
            "inlierCount": int(sum(bool(value) for value in inliers)),
            "metrics": profile.metrics.to_dict(),
            "visualizations": {
                "rgbCorners": str(output_dir / "rgb_corners.png"),
                "msCorners": str(output_dir / "ms_corners.png"),
                **visual_paths,
            },
            "validationPairs": _validation_diagnostics(detected[1:], profile.matrixRgbToMultispectral),
            "scientificBoundary": (
                "Planar homography is calibrated for one checkerboard plane near representative fruit center depth. "
                "It is not a perfect 3D pixel registration for spherical fruit; production ROI should use conservative inward erosion."
            ),
        }
        diagnostics_path = output_dir / "diagnostics.json"
        diagnostics_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("RGB-DVP2 registration calibration completed.")
        print(f"profile: {profile_path}")
        print(f"diagnostics: {diagnostics_path}")
        print(f"rmsePx: {profile.metrics.rmsePx:.4f}; meanErrorPx: {profile.metrics.meanErrorPx:.4f}; maxErrorPx: {profile.metrics.maxErrorPx:.4f}")
        return 0
    except RegistrationError as exc:
        print(f"Registration error: {exc}")
        return 2


def _collect_pairs(args: argparse.Namespace) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    if args.rgb_image and args.ms_image:
        pairs.append({"name": "pair_001", "rgbPath": str(args.rgb_image), "msPath": str(args.ms_image)})
    for raw in args.pair:
        if "=" not in raw or "," not in raw:
            raise RegistrationError(f"invalid --pair format: {raw}")
        name, paths = raw.split("=", 1)
        rgb_path, ms_path = paths.split(",", 1)
        pairs.append({"name": name.strip(), "rgbPath": rgb_path.strip(), "msPath": ms_path.strip()})
    return pairs


def _pair_metadata(pair: dict[str, Any], *, include_reference: bool) -> dict[str, Any]:
    return {
        "name": pair["name"],
        "rgbPath": str(pair["rgbPath"]),
        "multispectralPath": str(pair["msPath"]),
        "role": "reference" if include_reference else "validation",
        "rgbImageShape": list(pair["rgbImageShape"]),
        "msImageShape": list(pair["msImageShape"]),
    }


def _validation_diagnostics(pairs: list[dict[str, Any]], homography: list[list[float]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        errors = compute_reprojection_errors(pair["rgbCorners"], pair["msCorners"], homography)
        metrics = reprojection_metrics(errors)
        rows.append({
            "name": pair["name"],
            "metrics": metrics.to_dict(),
        })
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
