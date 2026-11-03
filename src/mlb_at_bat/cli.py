"""Clean baseball data and run the season-repertoire pitch-selection baseline."""

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError

from .data import SAMPLE_URL, clean_exports, fetch_csv, fetch_dates
from .pitcher_seasons import clean_pitcher_summary
from .pitcher_exports import clean_pitcher_exports
from .hitter_exports import clean_hitter_exports, KINDS as HITTER_KINDS
from .pitch_selection import compare_pitch_frequencies, load_pitch_baseline, write_pitch_baseline


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="Download regular-season Savant exports, one day per request")
    fetch.add_argument("--start", type=date.fromisoformat, required=True)
    fetch.add_argument("--end", type=date.fromisoformat, required=True)
    fetch.add_argument("--output", type=Path, default=Path("data/raw/savant"))
    sample = commands.add_parser("sample", help="Download a pinned historical test fixture, NOT live data")
    sample.add_argument("--output", type=Path, default=Path("data/raw/historical_sample"))
    clean = commands.add_parser("clean", help="Clean exports and write Parquet tables plus a quality report")
    clean.add_argument("--input", type=Path, nargs="+", required=True, help="CSV files or directories of CSV files")
    clean.add_argument("--output", type=Path, required=True)
    pitchers = commands.add_parser("clean-pitchers", help="Archive and clean an uploaded pitcher-season summary")
    pitchers.add_argument("--input", type=Path, required=True)
    pitchers.add_argument("--raw-output", type=Path, default=Path("data/raw/pitcher_seasons/stats.csv"))
    pitchers.add_argument("--output", type=Path, required=True)
    pitchers.add_argument("--min-innings", type=int, default=10)
    exports = commands.add_parser("clean-pitcher-exports", help="Clean five pitcher exports and build a master view")
    for source in ["stats", "bat-tracking", "contact", "location", "arsenal"]:
        exports.add_argument(f"--{source}", type=Path, required=True)
    exports.add_argument("--raw-output", type=Path, required=True)
    exports.add_argument("--output", type=Path, required=True)
    exports.add_argument("--min-innings", type=int, default=10)
    hitters = commands.add_parser("clean-hitter-exports", help="Clean hitter and specialist summaries without discarding low-PA players")
    for source in HITTER_KINDS:
        hitters.add_argument('--'+source.replace('_','-'),type=Path,required=True)
    hitters.add_argument('--raw-output',type=Path,required=True)
    hitters.add_argument('--output',type=Path,required=True)
    hitters.add_argument('--min-pa',type=int,default=100)
    baseline = commands.add_parser("build-pitch-baseline", help="Export pitcher repertoire probabilities and coverage")
    simulate = commands.add_parser("sample-pitches", help="Sample pitch types for one pitcher-season, not at-bats")
    for command in (baseline, simulate):
        command.add_argument("--arsenal", type=Path,
            default=Path("datasets/2026/views/pitch_arsenal_types.csv"))
        command.add_argument("--pitchers", type=Path,
            default=Path("datasets/2026/views/pitchers.csv"))
    baseline.add_argument("--output", type=Path, default=Path("data/processed/pitch_selection_2026"))
    simulate.add_argument("--player-id", type=int, required=True)
    simulate.add_argument("--season", type=int, required=True)
    simulate.add_argument("--n-pitches", type=int, default=10000)
    simulate.add_argument("--seed", type=int, default=None)
    simulate.add_argument("--output", type=Path,
        help="Optional directory for sampled_pitches.csv and frequency_comparison.csv")
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            manifests = fetch_dates(args.start, args.end, args.output)
            print(json.dumps({"daily_files": len(manifests), "rows": sum(m["rows"] for m in manifests)}))
        elif args.command == "sample":
            manifest = fetch_csv(SAMPLE_URL, args.output / "2020-09-03.csv",
                source_type="historical_pybaseball_test_fixture")
            print(json.dumps({"source_type": manifest["source_type"], "rows": manifest["rows"],
                "sha256": manifest["sha256"]}))
        elif args.command == "clean-pitchers":
            report = clean_pitcher_summary(args.input, args.output, raw_output=args.raw_output,
                                           min_innings=args.min_innings)
            print(json.dumps({k: report[k] for k in ["dataset_type", "input_rows", "input_columns",
                "clean_rows", "unique_pitchers", "seasons", "rejected_rows", "flagged_pitchers"]}, indent=2))
            print(f"Quality report: {args.output / 'quality_report.json'}")
        elif args.command == "clean-pitcher-exports":
            inputs = {name: getattr(args,name) for name in ["stats","bat_tracking","contact","location","arsenal"]}
            report = clean_pitcher_exports(inputs, args.raw_output, args.output, args.min_innings)
            print(json.dumps({k:report[k] for k in ["master_pitchers","master_columns","seasons","arsenal_pitch_type_rows"]},indent=2))
            print(f"Quality report: {args.output / 'quality_report.json'}")
        elif args.command == "clean-hitter-exports":
            report=clean_hitter_exports({k:getattr(args,k) for k in HITTER_KINDS},args.raw_output,args.output,args.min_pa)
            print(json.dumps({k:report[k] for k in ['master_rows','master_columns','meets_min_pa','below_min_pa_retained','flagged_check_rows']},indent=2))
            print(f"Quality report: {args.output / 'quality_report.json'}")
        elif args.command == "build-pitch-baseline":
            report = write_pitch_baseline(args.arsenal, args.pitchers, args.output)
            print(json.dumps({k: report[k] for k in ["pitcher_seasons", "pitch_type_rows",
                "available_pitcher_seasons", "quality_status_counts"]}, indent=2))
            print(f"Baseline tables and quality report: {args.output}")
        elif args.command == "sample-pitches":
            baseline_model = load_pitch_baseline(args.arsenal, args.pitchers)
            repertoire = baseline_model.repertoire(args.player_id, args.season)
            samples = baseline_model.sample_pitches(args.player_id, args.season,
                n_pitches=args.n_pitches, seed=args.seed)
            comparison = compare_pitch_frequencies(repertoire, samples)
            print(f"{repertoire.player_name.iloc[0]} ({args.player_id}/{args.season}): "
                  f"{len(samples)} independent pitch selections; seed={args.seed}")
            print(comparison[["pitch_type", "usage_percent", "sampling_probability",
                "sampled_count", "sampled_percent", "difference_percentage_points"]].to_string(index=False))
            if args.output is not None:
                if any(args.output.resolve() == p.resolve().parent or args.output.resolve() in p.resolve().parents
                       for p in (args.arsenal, args.pitchers)):
                    raise ValueError("Output must not contain a source input")
                args.output.mkdir(parents=True, exist_ok=True)
                samples.to_csv(args.output / "sampled_pitches.csv", index=False, lineterminator="\n")
                comparison.to_csv(args.output / "frequency_comparison.csv", index=False, lineterminator="\n")
                metadata = {"player_id": args.player_id, "season": args.season,
                    "n_pitches": args.n_pitches, "seed": args.seed,
                    "sources": {name: {"filename": path.name,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        for name, path in [("arsenal", args.arsenal), ("pitchers", args.pitchers)]}}
                (args.output / "simulation.json").write_text(
                    json.dumps(metadata, indent=2) + "\n", encoding="utf-8", newline="\n"
                )
                print(f"Simulation tables: {args.output}")
        else:
            inputs = []
            for path in args.input:
                inputs.extend(sorted(path.glob("*.csv")) if path.is_dir() else [path])
            report = clean_exports(inputs, args.output)
            print(json.dumps({k: report[k] for k in ["input_rows", "clean_pitch_rows", "rejected_rows",
                "exact_duplicate_rows_removed", "games", "plate_appearance_groups",
                "eligible_completed_plate_appearances"]}, indent=2))
            print(f"Quality report: {args.output / 'quality_report.json'}")
    except (ValueError, OSError, HTTPError, URLError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
