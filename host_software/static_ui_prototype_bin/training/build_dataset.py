from __future__ import annotations

import argparse
from pathlib import Path

from quality_algorithm.dataset import build_feature_rows, write_feature_csv
from quality_algorithm.filters import expected_wavelengths, load_filter_config


def build_dataset(samples_root: str | Path, labels_csv: str | Path, output_csv: str | Path, *, filter_config: str | Path | None = None) -> Path:
    filters = load_filter_config(filter_config)
    rows = build_feature_rows(samples_root, labels_csv, filters=filters, allow_uncalibrated=True)
    write_feature_csv(rows, output_csv, expected_wavelengths(filters))
    return Path(output_csv)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build multispectral feature CSV from sample folders and labels.csv.")
    parser.add_argument("--samples", required=True, help="Folder containing sample_*/rgb and sample_*/multispectral folders.")
    parser.add_argument("--labels", required=True, help="CSV with sample_id,ssc,ta,ph columns.")
    parser.add_argument("--output", required=True, help="Output feature CSV path.")
    parser.add_argument("--filter-config", default=None, help="Optional filter configuration JSON.")
    args = parser.parse_args()
    try:
        output = build_dataset(args.samples, args.labels, args.output, filter_config=args.filter_config)
    except Exception as exc:
        print(f"Insufficient training dataset: {exc}")
        return 2
    print(f"feature dataset written: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

