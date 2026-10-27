"""Clean related pitcher-season exports and build a validated, derived master view."""

from __future__ import annotations

import csv
import io
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .data import NULLS, _write_new, sha256
from .pitcher_seasons import NAME, archive_upload, clean_pitcher_summary

KEY = ["player_id", "season"]
SOURCES = ("stats", "bat_tracking", "contact", "location", "arsenal")
PITCH_TYPES = {"ff": "Four-seam fastball", "sl": "Slider", "ch": "Changeup",
    "cu": "Curveball", "si": "Sinker", "fc": "Cutter", "fs": "Split-finger",
    "kn": "Knuckleball", "st": "Sweeper", "sv": "Slurve", "fo": "Forkball", "sc": "Screwball"}
FAMILIES = ("fastball", "breaking", "offspeed")
ARSENAL_METRICS = ("avg_speed", "avg_spin", "avg_break_x", "avg_break_z",
                   "avg_break_z_induced", "avg_break", "range_speed")
SCHEMAS = {
    "bat_tracking": {
        "counts": {"swords"},
        "percentages": set("fast_swing_rate blasts_contact blasts_swing squared_up_contact "
                           "squared_up_swing ideal_angle_rate".split()),
        "floats": set("avg_swing_speed avg_swing_length attack_angle attack_direction vertical_swing_path".split()),
    },
    "contact": {
        "counts": {"barrel"},
        "percentages": set("sweet_spot_percent barrel_batted_rate solidcontact_percent "
            "flareburner_percent poorlyunder_percent poorlytopped_percent poorlyweak_percent hard_hit_percent".split()),
        "floats": set("exit_velocity_avg launch_angle_avg avg_best_speed avg_hyper_speed".split()),
    },
    "location": {
        "counts": set("out_zone_swing_miss out_zone_swing out_zone pitch_count_offspeed "
            "pitch_count_fastball pitch_count_breaking pitch_count in_zone_swing_miss "
            "in_zone_swing in_zone edge batted_ball groundballs flyballs linedrives popups".split()),
        "percentages": set("z_swing_percent z_swing_miss_percent oz_swing_percent "
            "oz_swing_miss_percent oz_contact_percent out_zone_percent meatball_swing_percent "
            "meatball_percent iz_contact_percent in_zone_percent edge_percent whiff_percent "
            "swing_percent pull_percent straightaway_percent opposite_percent f_strike_percent "
            "groundballs_percent flyballs_percent linedrives_percent popups_percent".split()),
        "floats": set(),
    },
    "arsenal": {
        "counts": {"n"},
        "percentages": {f"n_{p}_formatted" for p in [*PITCH_TYPES, *FAMILIES]},
        "floats": {"arm_angle"} | {f"{p}_{m}" for p in [*PITCH_TYPES, *FAMILIES] for m in ARSENAL_METRICS},
    },
}
SHARED_METRICS = {"contact": {"hard_hit_percent", "avg_best_speed", "avg_hyper_speed"},
                  "location": {"whiff_percent", "swing_percent"}}


def read_export(path: Path, kind: str) -> tuple[pd.DataFrame, dict]:
    """Parse original headers and strictly validate known units and join keys."""
    content = path.read_bytes()
    reader = csv.reader(io.StringIO(content.decode("utf-8-sig")), strict=True)
    try:
        headers = [h.strip() for h in next(reader)]
        if len(set(headers)) != len(headers) or any(not h or h.startswith("_") for h in headers):
            raise ValueError(f"{kind}: duplicate, blank, or reserved headers")
        if not {NAME, "player_id", "year"}.issubset(headers):
            raise ValueError(f"{kind}: missing name/player_id/year columns")
        if {"player_name", "season", "arsenal_pitch_total"} & set(headers):
            raise ValueError(f"{kind}: source collides with canonical output columns")
        records = []
        for number, row in enumerate(reader, 1):
            if not row:
                continue
            if len(row) != len(headers):
                raise ValueError(f"{kind}: malformed CSV record {number}")
            records.append(row)
    except StopIteration as exc:
        raise ValueError(f"{kind}: empty CSV") from exc
    except csv.Error as exc:
        raise ValueError(f"{kind}: malformed CSV: {exc}") from exc
    frame = pd.DataFrame(records, columns=headers, dtype="string")
    for c in frame:
        frame[c] = frame[c].str.strip()
        frame[c] = frame[c].mask(frame[c].str.lower().isin(NULLS))
    schema = SCHEMAS[kind]
    known = schema["counts"] | schema["percentages"] | schema["floats"]
    # An unexpected export should not silently pass as an all-missing category.
    if not known.intersection(headers):
        raise ValueError(f"{kind}: no recognized metric columns")
    all_missing = sorted(c for c in frame if frame[c].isna().all())
    for c in sorted(({"player_id", "year"} | known) & set(frame)):
        value = pd.to_numeric(frame[c], errors="coerce")
        integer = c in schema["counts"] or c in {"player_id", "year"}
        bad = frame[c].notna() & (value.isna() | ~np.isfinite(value.astype(float)))
        if integer:
            bad |= value.notna() & ((value % 1 != 0) | (value.abs() > 2**53-1) | (value < 0))
        if c in schema["percentages"]:
            bad |= value.notna() & ~value.between(0, 100)
        if bad.any():
            examples = (frame.index[bad][:3] + 1).tolist()
            raise ValueError(f"{kind}: invalid numeric/unit values in {c}, source records {examples}")
        frame[c] = value.astype("Int64" if integer else "Float64")
    for c in ["player_id", "year"]:
        if frame[c].isna().any() or frame[c].le(0).any():
            raise ValueError(f"{kind}: missing or invalid {c}")
    if frame.duplicated(["player_id", "year"]).any():
        raise ValueError(f"{kind}: duplicate player-season keys; resolve before joining")
    rename = {NAME: "player_name", "year": "season"}
    if kind == "arsenal":
        rename["n"] = "arsenal_pitch_total"
        rename.update({f"n_{p}_formatted": f"{p}_usage_percent" for p in [*PITCH_TYPES, *FAMILIES]})
        if set(rename.values()) & (set(headers) - {NAME, "year", "n"}):
            raise ValueError("arsenal: source collides with renamed usage columns")
        if "pitch_hand" in frame:
            frame["pitch_hand"] = frame["pitch_hand"].str.upper()
            if (~frame.pitch_hand.isin(["L", "R", "S"]) & frame.pitch_hand.notna()).any():
                raise ValueError("arsenal: unknown pitching hand")
    frame = frame.rename(columns=rename)
    frame["_source_record"] = pd.Series(range(1, len(frame)+1), dtype="Int64")
    frame = frame.sort_values(KEY, kind="stable").reset_index(drop=True)
    meta = {"rows": len(frame), "source_columns": headers, "sha256": sha256(content),
        "all_missing_source_columns": all_missing,
        "unknown_columns_preserved_as_strings": sorted(set(headers) - known
            - {NAME, "player_id", "year", "pitch_hand"}),
        "missing_expected_metrics": sorted(known - set(headers)),
        "renamed_columns": {k: v for k, v in rename.items() if k in headers}}
    return frame, meta


def archive_export(source: Path, destination: Path, kind: str) -> dict:
    _, audit = read_export(source, kind)
    content = source.read_bytes()
    manifest_path = destination.with_suffix(".manifest.json")
    if destination.exists() or manifest_path.exists():
        if not destination.exists() or not manifest_path.exists():
            raise ValueError(f"{kind}: incomplete raw archive; choose a new raw directory")
        old = json.loads(manifest_path.read_text())
        if (sha256(destination.read_bytes()) != audit["sha256"] or old["sha256"] != audit["sha256"]
                or old.get("category") != kind):
            raise ValueError(f"{kind}: raw source changed; refusing to overwrite it")
        return old
    manifest = {"source_type": "user_uploaded_pitcher_export", "category": kind,
        "original_filename": source.name, "sha256": audit["sha256"], "rows": audit["rows"],
        "bytes": len(content), "archived_at_utc": datetime.now(timezone.utc).isoformat(),
        "export_url": None, "data_as_of": None, "game_type": None}
    _write_new(destination, content)
    _write_new(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())
    return manifest


def arsenal_long(arsenal: pd.DataFrame) -> pd.DataFrame:
    """One row per reported pitch type; broad families are not extra pitch types."""
    columns = KEY + ["player_name", "pitch_hand", "pitch_type", "pitch_name", "usage_percent",
        "usage_fraction", "arsenal_pitch_total", "arm_angle", *ARSENAL_METRICS, "_source_record"]
    rows = []
    for _, record in arsenal.iterrows():
        for code, name in PITCH_TYPES.items():
            usage = record.get(f"{code}_usage_percent", pd.NA)
            if pd.isna(usage):
                continue
            row = {c: record.get(c, pd.NA) for c in KEY +
                   ["player_name", "pitch_hand", "arsenal_pitch_total", "arm_angle", "_source_record"]}
            row.update(pitch_type=code.upper(), pitch_name=name, usage_percent=usage,
                       usage_fraction=usage / 100)
            row.update({m: record.get(f"{code}_{m}", pd.NA) for m in ARSENAL_METRICS})
            rows.append(row)
    result = pd.DataFrame(rows, columns=columns)
    for c in KEY + ["arsenal_pitch_total", "_source_record"]:
        result[c] = result[c].astype("Int64")
    for c in ["usage_percent", "usage_fraction", "arm_angle", *ARSENAL_METRICS]:
        result[c] = result[c].astype("Float64")
    for c in ["player_name", "pitch_hand", "pitch_type", "pitch_name"]:
        result[c] = result[c].astype("string")
    return result.sort_values(KEY + ["pitch_type"]).reset_index(drop=True)


def build_master(stats: pd.DataFrame, tables: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    if stats.duplicated(KEY).any():
        raise ValueError("stats: duplicate master keys")
    master = stats.copy()
    report = {"coverage": {}, "shared_metrics": {}, "name_disagreements": {}}
    base_keys = pd.MultiIndex.from_frame(stats[KEY])
    for name, table in tables.items():
        if table.duplicated(KEY).any():
            raise ValueError(f"{name}: duplicate keys cannot be joined")
        source_keys = pd.MultiIndex.from_frame(table[KEY])
        report["coverage"][name] = {"source_rows": len(table),
            "matched_stats_rows": len(base_keys.intersection(source_keys)),
            "missing_stats_rows": len(base_keys.difference(source_keys)),
            "outside_stats_cohort": len(source_keys.difference(base_keys))}
        comparison = stats[KEY + ["player_name"]].merge(table, on=KEY, how="inner",
                                                          validate="one_to_one", suffixes=("_stats", ""))
        report["name_disagreements"][name] = int(comparison.player_name.ne(comparison.player_name_stats).fillna(False).sum())
        skip = set()
        for metric in sorted(SHARED_METRICS.get(name, set()) & set(table) & set(stats)):
            compared = stats[KEY + [metric]].merge(table[KEY + [metric]], on=KEY,
                         validate="one_to_one", suffixes=("_stats", "_source"))
            a, b = compared[f"{metric}_stats"], compared[f"{metric}_source"]
            both = a.notna() & b.notna()
            same = (a.isna() & b.isna()) | (both & (a-b).abs().le(1e-8))
            agree = bool(same.all())
            report["shared_metrics"][f"{name}.{metric}"] = {"compared_rows": len(compared),
                "differing_rows": int((~same).sum()), "action": "use_stats_column" if agree
                else "preserve_both_source_columns"}
            if agree:
                skip.add(metric)
        extra = table.drop(columns=["player_name", *skip]).copy()
        extra[f"has_{name}"] = True
        extra = extra.rename(columns={c: f"{name}__{c.lstrip('_')}" for c in extra
                                     if c not in KEY and c != f"has_{name}"})
        master = master.merge(extra, on=KEY, how="left", validate="one_to_one")
        master[f"has_{name}"] = master[f"has_{name}"].astype("boolean").fillna(False)
    assert len(master) == len(stats) and not master.duplicated(KEY).any()
    return master.sort_values(KEY).reset_index(drop=True), report


def _differences(left: pd.DataFrame, right: pd.DataFrame, left_col: str, right_col: str) -> dict:
    if left_col not in left or right_col not in right:
        return {"status": "not_run", "missing_columns": [c for f, c in [(left,left_col),(right,right_col)] if c not in f]}
    joined = left[KEY + [left_col]].merge(right[KEY + [right_col]], on=KEY, validate="one_to_one",
                                        suffixes=("_left", "_right"))
    a = joined[left_col if left_col != right_col else left_col + "_left"]
    b = joined[right_col if left_col != right_col else right_col + "_right"]
    valid = a.notna() & b.notna()
    diff = (a-b).loc[valid]
    bad = valid & a.ne(b)
    details = joined.loc[bad, KEY].copy()
    details["left_value"] = a.loc[bad]
    details["right_value"] = b.loc[bad]
    details["difference"] = (a-b).loc[bad]
    return {"status": "compared", "evaluated_rows": int(valid.sum()),
        "different_rows": int(diff.ne(0).sum()),
        "left_minus_right_min": float(diff.min()) if len(diff) else None,
        "left_minus_right_max": float(diff.max()) if len(diff) else None,
        "left_field": left_col, "right_field": right_col,
        "details": details.to_dict(orient="records"),
        "note": "Source totals retained; differences can reflect metric coverage or definitions."}


def clean_pitcher_exports(inputs: dict[str, Path], raw: Path, output: Path,
                          min_innings: int = 10) -> dict:
    if min_innings < 0:
        raise ValueError("Minimum innings must be nonnegative")
    if set(inputs) != set(SOURCES):
        raise ValueError(f"Exactly these sources are required: {', '.join(SOURCES)}")
    raw, output = raw.resolve(), output.resolve()
    if raw == output or raw in output.parents or output in raw.parents:
        raise ValueError("Raw and processed directories must be separate")
    if any(output == p.resolve().parent or output in p.resolve().parents for p in inputs.values()):
        raise ValueError("Processed output must not contain an input source")
    archives = {k: raw / f"{k}.csv" for k in SOURCES}
    manifests = {"stats": archive_upload(inputs["stats"], archives["stats"])}
    tables, audits = {}, {}
    for kind in SOURCES[1:]:
        manifests[kind] = archive_export(inputs[kind], archives[kind], kind)
        tables[kind], audits[kind] = read_export(archives[kind], kind)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pitcher-exports-", dir=output.parent) as temp:
        stage = Path(temp)
        base_report = clean_pitcher_summary(archives["stats"], stage / "stats",
            raw_output=archives["stats"], min_innings=min_innings)
        stats = pd.read_parquet(stage / "stats/pitcher_profiles.parquet")
        master, joins = build_master(stats, tables)
        long = arsenal_long(tables["arsenal"])
        for kind, table in tables.items():
            table.to_parquet(stage / f"{kind}.parquet", index=False)
            table.to_csv(stage / f"{kind}.csv", index=False)
        count_comparisons = {
            "location_vs_stats_total_pitches": _differences(tables["location"], stats, "pitch_count", "p_total_pitches"),
            "arsenal_vs_location_total_pitches": _differences(tables["arsenal"], tables["location"], "arsenal_pitch_total", "pitch_count"),
        }
        for label, columns in {"unassigned_zone_pitches": ["in_zone", "out_zone"],
            "unassigned_pitch_family": ["pitch_count_fastball", "pitch_count_breaking", "pitch_count_offspeed"]}.items():
            location = tables["location"]
            if set(columns + ["pitch_count"]).issubset(location):
                subtotal = location[KEY].copy()
                subtotal["subtotal"] = location[columns].sum(axis=1, min_count=len(columns))
                count_comparisons[label] = _differences(subtotal, location, "subtotal", "pitch_count")
        difference_rows = [{"check": name, **row} for name, comparison in count_comparisons.items()
                           for row in comparison.get("details", [])]
        differences = pd.DataFrame(difference_rows, columns=["check", *KEY, "left_value", "right_value", "difference"])
        for key in KEY:
            differences[key] = differences[key].astype("Int64")
        differences = differences.merge(stats[KEY + ["player_name"]], on=KEY,
                                         how="left", validate="many_to_one")
        differences.to_csv(stage / "source_differences.csv", index=False)
        difference_keys = set(differences[KEY].itertuples(index=False, name=None))
        master["has_source_total_difference"] = pd.Series(
            [key in difference_keys for key in master[KEY].itertuples(index=False, name=None)], dtype="boolean")
        for name, table in [("pitcher_master", master), ("pitch_arsenal_types", long)]:
            table.to_parquet(stage / f"{name}.parquet", index=False)
            table.to_csv(stage / f"{name}.csv", index=False)
        usage = long.groupby(KEY).usage_percent.agg(["sum", "count"])
        usage_check = {"pitcher_groups": len(usage),
            "outside_rounding_tolerance": int((usage["sum"]-100).abs().gt(usage["count"]*.05 + 1e-7).sum()),
            "sum_min": float(usage["sum"].min()) if len(usage) else None,
            "sum_max": float(usage["sum"].max()) if len(usage) else None,
            "note": "Rounded percentages retained. No exact per-type pitch counts are reconstructed."}
        dictionary = []
        for kind, table in {"stats": stats, **tables, "pitch_arsenal_types": long}.items():
            reverse = {v:k for k,v in audits.get(kind,{}).get("renamed_columns",{}).items()}
            for column in table:
                if kind == "stats":
                    origin = base_report["columns"].get(column, {}).get("source_column")
                elif kind in audits:
                    candidate = reverse.get(column, column)
                    origin = candidate if candidate in audits[kind]["source_columns"] else None
                else:
                    origin = None  # long-format fields are transformations of type-specific columns
                dictionary.append({"table":kind,"column":column,"source_column":origin,
                    "dtype":str(table[column].dtype),"missing":int(table[column].isna().sum())})
        pd.DataFrame(dictionary).to_csv(stage / "data_dictionary.csv", index=False)
        report = {"schema_version":1,"minimum_innings":min_innings,"sources":manifests,
            "master_pitchers":len(master),"master_columns":len(master.columns),
            "seasons":sorted(master.season.unique().tolist()),"arsenal_pitch_type_rows":len(long),
            "stats_quality_summary":{k:base_report[k] for k in ["clean_rows","rejected_rows","flagged_pitchers"]},
            "source_audits":audits,"joins":joins,"count_comparisons":count_comparisons,
            "source_difference_rows":len(differences),"pitchers_with_source_total_differences":len(difference_keys),
            "arsenal_usage":usage_check,"notes":[
                "Stats is the master cohort; advanced source rows outside it remain in their category tables.",
                "Raw files are immutable; earlier qualified-only exports are not mixed into this batch.",
                "Known equal shared metrics use the stats value in the master; differing values are preserved separately.",
                "Missing arsenal fields remain missing; broad pitch families are not extra individual pitch types.",
                "Signed movement and angle values are preserved, including negative values.",
                "Pitch totals with different definitions are not forced to agree.",
                "Season labels and export thresholds are supplied metadata; game type and as-of date are unverified.",
                "These are season summaries, not pitch sequences; no model is trained by this command."]}
        (stage / "quality_report.json").write_text(json.dumps(report,indent=2)+"\n")
        output.mkdir(parents=True, exist_ok=True)
        # Publish the report last so it marks a completed generated snapshot.
        paths = sorted(p for p in stage.rglob("*") if p.is_file())
        final_report = stage / "quality_report.json"
        for path in [p for p in paths if p != final_report] + [final_report]:
            dest = output / path.relative_to(stage)
            dest.parent.mkdir(parents=True, exist_ok=True)
            path.replace(dest)
    return report
