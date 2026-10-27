"""Clean hitter profiles and retain specialist measurements as separate tables."""

import csv
import io
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .data import NULLS, _write_new, sha256
from .pitcher_exports import KEY, SCHEMAS

NAME = "last_name, first_name"
KINDS = ("standard", "statcast", "contact", "location", "bat_tracking",
         "catcher_poptime", "catch_probability", "jump", "running")
CORE = KINDS[:5]
PERCENTAGES = {"k_percent", "bb_percent", "r_stolen_base_pct"}
STANDARD_FLOATS = set("batting_avg slg_percent on_base_percent on_base_plus_slg isolated_power babip".split())
STANDARD_COUNTS = set("player_age ab pa hit single double triple home_run strikeout walk b_rbi b_lob "
    "b_total_bases r_total_caught_stealing r_total_stolen_base b_ab_scoring b_ball b_called_strike "
    "b_catcher_interf b_foul b_foul_tip b_game b_gnd_into_dp b_gnd_into_tp b_gnd_rule_double "
    "b_hit_by_pitch b_hit_ground b_hit_fly b_hit_into_play b_hit_line_drive b_hit_popup b_out_fly "
    "b_out_ground b_out_line_drive b_out_popup b_intent_ball b_intent_walk b_interference "
    "b_pinch_hit b_pinch_run b_pitchout b_played_dh b_sac_bunt b_sac_fly b_swinging_strike "
    "r_caught_stealing_2b r_caught_stealing_3b r_caught_stealing_home r_defensive_indiff "
    "r_interference r_pickoff_1b r_pickoff_2b r_pickoff_3b r_run r_stolen_base_2b "
    "r_stolen_base_3b r_stolen_base_home b_total_ball b_total_sacrifices b_total_strike "
    "b_total_swinging_strike b_total_pitches r_total_pickoff b_reached_on_error b_walkoff b_reached_on_int".split())
SPECS = {
    "standard": (STANDARD_COUNTS, PERCENTAGES, STANDARD_FLOATS),
    "statcast": (set(), set(), set("xba xslg woba xwoba xobp xiso wobacon xwobacon bacon xbacon xbadiff xslgdiff wobadiff".split())),
    "catcher_poptime": ({"pop_2b_sba_count", "pop_3b_sba_count"}, set(),
        set("pop_2b_sba pop_2b_sb pop_2b_cs pop_3b_sba pop_3b_sb pop_3b_cs exchange_2b_3b_sba maxeff_arm_2b_3b_sba".split())),
    "catch_probability": ({"n_outs_above_average"} | {f"n_{p}_{s}stars" for p in ["fieldout", "opp"] for s in range(1,6)},
        {f"n_{s}star_percent" for s in range(1,6)}, set()),
    "jump": (set(), set(), set("rel_league_reaction_distance rel_league_burst_distance rel_league_routing_distance rel_league_bootup_distance f_bootup_distance".split())),
    "running": ({"n_bolts"}, set(), {"hp_to_1b"}),
}
for _kind in ["contact", "location", "bat_tracking"]:
    _s = SCHEMAS[_kind]
    SPECS[_kind] = (_s["counts"], _s["percentages"], _s["floats"])


def read_hitter_export(path, kind):
    content = Path(path).read_bytes()
    reader = csv.reader(io.StringIO(content.decode("utf-8-sig")), strict=True)
    try:
        headers = [h.strip() for h in next(reader)]
        if (len(set(headers)) != len(headers) or not {NAME,"player_id","year"}.issubset(headers)
                or any(not h or h.startswith("_") for h in headers)
                or {"player_name","season","has_metrics","meets_min_pa"}.intersection(headers)):
            raise ValueError(f"{kind}: missing, duplicate, or reserved headers")
        rows = []
        for n,row in enumerate(reader,1):
            if not row: continue
            if len(row) != len(headers): raise ValueError(f"{kind}: malformed CSV record {n}")
            rows.append(row)
    except (StopIteration,csv.Error) as exc:
        raise ValueError(f"{kind}: invalid CSV") from exc
    f = pd.DataFrame(rows,columns=headers,dtype="string")
    for c in f:
        f[c] = f[c].str.strip()
        f[c] = f[c].mask(f[c].str.lower().isin(NULLS))
    counts, percentages, floats = SPECS[kind]
    known = counts | percentages | floats
    if not known.intersection(headers): raise ValueError(f"{kind}: no recognized measurements")
    if kind == "standard" and "pa" not in f: raise ValueError("standard: missing plate appearances")
    metric_cols = sorted(known.intersection(headers))
    for c in sorted((known | {"player_id","year"}).intersection(headers)):
        v = pd.to_numeric(f[c],errors="coerce")
        integer = c in counts or c in {"player_id","year"}
        bad = f[c].notna() & (v.isna() | ~np.isfinite(v.astype(float)))
        if integer:
            bad |= v.notna() & ((v % 1 != 0) | (v.abs() > 2**53-1))
            if c != "n_outs_above_average": bad |= v.lt(0).fillna(False)
        if c in percentages: bad |= (v.notna() & ~v.between(0,100))
        if bad.any(): raise ValueError(f"{kind}: invalid numeric values in {c}")
        f[c] = v.astype("Int64" if integer else "Float64")
    for c in ["player_id","year"]:
        if f[c].isna().any() or f[c].le(0).any(): raise ValueError(f"{kind}: invalid {c}")
    if f.duplicated(["player_id","year"]).any(): raise ValueError(f"{kind}: duplicate player-season keys")
    f["_source_record"] = pd.Series(range(1,len(f)+1),dtype="Int64")
    f["has_metrics"] = f[metric_cols].notna().any(axis=1).astype("boolean")
    f = f.rename(columns={NAME:"player_name","year":"season"}).sort_values(KEY).reset_index(drop=True)
    audit = {"rows":len(f),"source_columns":headers,"sha256":sha256(content),
        "rows_with_metrics":int(f.has_metrics.sum()),"rows_without_metrics":int((~f.has_metrics).sum()),
        "all_missing_columns":[c for c in metric_cols if f[c].isna().all()],
        "unknown_columns_preserved":sorted(set(headers)-known-{NAME,"player_id","year"}),
        "missing_expected_columns":sorted(known-set(headers))}
    return f,audit


def clean_hitter_exports(inputs, raw, output, min_pa=100):
    if set(inputs) != set(KINDS): raise ValueError(f"Provide all nine categories: {KINDS}")
    if min_pa < 0: raise ValueError("Minimum PA must be nonnegative")
    raw,output = Path(raw).resolve(),Path(output).resolve()
    if raw==output or raw in output.parents or output in raw.parents:
        raise ValueError("Raw and processed directories must be separate")
    if any(output == Path(p).resolve().parent or output in Path(p).resolve().parents for p in inputs.values()):
        raise ValueError("Processed directory must not contain a source file")
    tables,audits,manifests = {},{},{}
    for kind in KINDS:
        source = Path(inputs[kind]); f,audit = read_hitter_export(source,kind)
        dest = raw/f"{kind}.csv"; meta = dest.with_suffix('.manifest.json')
        if dest.exists() or meta.exists():
            if not dest.exists() or not meta.exists(): raise ValueError(f"{kind}: incomplete raw archive")
            manifest = json.loads(meta.read_text())
            if manifest.get('category')!=kind or manifest['sha256']!=audit['sha256'] or sha256(dest.read_bytes())!=audit['sha256']:
                raise ValueError(f"{kind}: refusing to overwrite changed raw data")
        else:
            manifest={"source_type":"user_uploaded_hitter_export","category":kind,"original_filename":source.name,
                "sha256":audit['sha256'],"rows":len(f),"archived_at_utc":datetime.now(timezone.utc).isoformat(),
                "export_url":None,"game_type":None,"data_as_of":None}
            _write_new(dest,source.read_bytes()); _write_new(meta,(json.dumps(manifest,indent=2)+'\n').encode())
        tables[kind],audits[kind],manifests[kind]=f,audit,manifest
    base=tables['standard'].copy()
    if base.pa.isna().any() or base.pa.le(0).any(): raise ValueError('standard: missing/nonpositive PA')
    base['meets_min_pa']=base.pa.ge(min_pa).astype('boolean')
    master=base.copy(); coverage={}
    base_keys=pd.MultiIndex.from_frame(base[KEY])
    for kind in KINDS[1:]:
        f=tables[kind]; keys=pd.MultiIndex.from_frame(f[KEY])
        coverage[kind]={"matched_rows":len(base_keys.intersection(keys)),"missing_rows":len(base_keys.difference(keys)),
                        "outside_cohort":len(keys.difference(base_keys)),"rows_with_metrics":audits[kind]['rows_with_metrics']}
        names=base[KEY+['player_name']].merge(f[KEY+['player_name']],on=KEY,validate='one_to_one',suffixes=('_base','_source'))
        coverage[kind]['name_disagreements']=int(names.player_name_base.ne(names.player_name_source).fillna(False).sum())
        if kind not in CORE: continue
        extra=f.drop(columns='player_name').rename(columns={c:f'{kind}__{c.lstrip("_")}' for c in f if c not in KEY+['player_name']})
        extra[f'has_{kind}_row']=True
        master=master.merge(extra,on=KEY,how='left',validate='one_to_one')
        master[f'has_{kind}_row']=master[f'has_{kind}_row'].astype('boolean').fillna(False)
        master[f'{kind}__has_metrics']=master[f'{kind}__has_metrics'].astype('boolean').fillna(False)
    checks={}; flags=[]
    def check(label,columns,calculation):
        if not set(columns).issubset(base):
            checks[label]={"status":"not_run","missing_columns":sorted(set(columns)-set(base))}; return
        eligible=base[columns].notna().all(axis=1)
        bad=eligible & calculation(base).fillna(False)
        checks[label]={"evaluated_rows":int(eligible.sum()),"flagged_rows":int(bad.sum())}
        for row in base.loc[bad,KEY+['player_name']].to_dict('records'): flags.append({'check':label,**row})
    check('hit_components',['hit','single','double','triple','home_run'],lambda f:f.hit.ne(f.single+f.double+f.triple+f.home_run))
    check('total_bases',['b_total_bases','single','double','triple','home_run'],lambda f:f.b_total_bases.ne(f.single+2*f.double+3*f.triple+4*f.home_run))
    check('pitch_totals',['b_total_pitches','b_total_ball','b_total_strike'],lambda f:f.b_total_pitches.ne(f.b_total_ball+f.b_total_strike))
    for label,num in [('k_percent','strikeout'),('bb_percent','walk')]:
        check(label,[label,num,'pa'],lambda f,c=label,n=num:(f[c]-100*f[n]/f.pa).abs().gt(.0500001))
    for label,num in [('batting_avg','hit'),('slg_percent','b_total_bases')]:
        check(label,[label,num,'ab'],lambda f,c=label,n=num:f.ab.gt(0)&(f[c]-f[n]/f.ab).abs().gt(.0005001))
    report={'dataset_type':'hitter_and_specialist_summaries','sources':manifests,'source_audits':audits,
        'master_rows':len(master),'master_columns':len(master.columns),'seasons':sorted(base.season.unique().tolist()),
        'min_pa_flag_threshold':min_pa,'meets_min_pa':int(base.meets_min_pa.sum()),'below_min_pa_retained':int((~base.meets_min_pa).sum()),
        'pa_min':int(base.pa.min()) if len(base) else None,'pa_max':int(base.pa.max()) if len(base) else None,
        'coverage':coverage,'standard_checks':checks,'flagged_check_rows':len(flags),
        'notes':['All source players are retained; the PA threshold is a flag and a separate filtered view.',
            'Catcher, catch-probability, jump and running tables stay separate from the batting master.',
            'Source rows with no measurements remain nullable; file presence is distinct from measured coverage.',
            'Signed angles, expected-stat differences, OAA and relative jump metrics retain their signs.',
            'The running export contains bolts and home-to-first time, not a sprint-speed feet/second column.',
            'Filenames do not establish an innings or PA eligibility rule. Export settings are unverified.',
            'This batch contains summaries, not direct batter-pitcher or individual pitch-type performance splits.']}
    assert len(master)==len(base) and not master.duplicated(KEY).any()
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent,prefix='hitter-clean-') as tmp:
        stage=Path(tmp)
        for kind,f in {**tables,'hitter_master':master,'hitter_master_min_pa':master.loc[master.meets_min_pa]}.items():
            f.to_csv(stage/f'{kind}.csv',index=False);f.to_parquet(stage/f'{kind}.parquet',index=False)
        pd.DataFrame(flags,columns=['check',*KEY,'player_name']).to_csv(stage/'quality_flags.csv',index=False)
        dictionary=[{'table':kind,'column':c,'source_column':NAME if c=='player_name' else 'year' if c=='season'
            else c if c in audits[kind]['source_columns'] else None,
            'dtype':str(f[c].dtype),'missing':int(f[c].isna().sum())} for kind,f in tables.items() for c in f]
        pd.DataFrame(dictionary).to_csv(stage/'data_dictionary.csv',index=False)
        (stage/'quality_report.json').write_text(json.dumps(report,indent=2)+'\n')
        output.mkdir(parents=True,exist_ok=True)
        files=sorted(stage.iterdir()); final=stage/'quality_report.json'
        for p in [f for f in files if f!=final]+[final]:p.replace(output/p.name)
    return report
