import csv

import pandas as pd
import pytest

from mlb_at_bat.hitter_exports import KINDS, NAME, clean_hitter_exports, read_hitter_export


def write(path,rows):
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    return path


def row(player=1,**metrics):
    return {NAME:f'Player, {player}','player_id':player,'year':2026,**metrics}


def inputs(tmp_path):
    metrics={'standard':{'pa':50,'ab':40,'hit':10,'single':7,'double':2,'triple':0,'home_run':1,'b_total_bases':15},
        'statcast':{'xba':.25,'xbadiff':-.02},'contact':{'barrel':1,'launch_angle_avg':-5},
        'location':{'pitch_count':200},'bat_tracking':{'swords':2,'attack_direction':-3},
        'catcher_poptime':{'pop_2b_sba_count':None,'pop_2b_sba':None},
        'catch_probability':{'n_outs_above_average':-2,'n_opp_1stars':10},
        'jump':{'rel_league_reaction_distance':-1},'running':{'n_bolts':None,'hp_to_1b':4.1}}
    paths={}
    for kind in KINDS:
        a=row(1,**metrics[kind]);b=row(2,**metrics[kind])
        if kind=='standard':b['pa']=150
        paths[kind]=write(tmp_path/f'{kind}.csv',[a,b])
    return paths


def test_preserves_all_players_and_distinguishes_missing_measurements(tmp_path):
    paths=inputs(tmp_path);out=tmp_path/'cleaned'
    r=clean_hitter_exports(paths,tmp_path/'raw',out)
    assert r['master_rows']==2 and r['meets_min_pa']==1 and r['below_min_pa_retained']==1
    master=pd.read_parquet(out/'hitter_master.parquet')
    assert master.meets_min_pa.tolist()==[False,True]
    assert len(pd.read_csv(out/'hitter_master_min_pa.csv'))==1
    assert not any(c.startswith('catcher_poptime__') for c in master)
    catcher=pd.read_parquet(out/'catcher_poptime.parquet')
    assert len(catcher)==2 and not catcher.has_metrics.any()
    assert catcher.pop_2b_sba_count.isna().all()
    assert r['coverage']['catcher_poptime']['matched_rows']==2
    assert r['coverage']['catcher_poptime']['rows_with_metrics']==0


def test_signed_fields_and_original_nulls_survive(tmp_path):
    paths=inputs(tmp_path);out=tmp_path/'cleaned'
    clean_hitter_exports(paths,tmp_path/'raw',out)
    expected={'statcast':('xbadiff',-.02),'catch_probability':('n_outs_above_average',-2),
              'jump':('rel_league_reaction_distance',-1),'contact':('launch_angle_avg',-5)}
    for kind,(field,value) in expected.items():
        assert pd.read_parquet(out/f'{kind}.parquet')[field].tolist()==[value,value]
    assert pd.read_parquet(out/'running.parquet').n_bolts.isna().all()


def test_invalid_keys_and_percentages_fail_before_join(tmp_path):
    p=write(tmp_path/'bad.csv',[row(fast_swing_rate=101)])
    with pytest.raises(ValueError,match='invalid numeric'):read_hitter_export(p,'bat_tracking')
    p=write(p,[row(swords=2),row(swords=3)])
    with pytest.raises(ValueError,match='duplicate player-season'):read_hitter_export(p,'bat_tracking')


def test_source_integrity_and_repeatability(tmp_path):
    paths=inputs(tmp_path);before={k:p.read_bytes() for k,p in paths.items()}
    out=tmp_path/'cleaned';raw=tmp_path/'raw'
    first=clean_hitter_exports(paths,raw,out)
    assert clean_hitter_exports(paths,raw,out)==first
    assert all((raw/f'{k}.csv').read_bytes()==content for k,content in before.items())
    paths['running'].write_bytes(paths['running'].read_bytes()+b'\n')
    with pytest.raises(ValueError,match='refusing to overwrite'):clean_hitter_exports(paths,raw,out)
    assert (raw/'running.csv').read_bytes()==before['running']


def test_missing_core_category_rows_do_not_drop_players(tmp_path):
    paths=inputs(tmp_path)
    write(paths['contact'],[row(1,barrel=1)])
    out=tmp_path/'cleaned';r=clean_hitter_exports(paths,tmp_path/'raw',out)
    m=pd.read_parquet(out/'hitter_master.parquet')
    assert len(m)==2 and m.has_contact_row.tolist()==[True,False]
    assert m.contact__has_metrics.tolist()==[True,False]
    assert pd.isna(m.loc[1,'contact__barrel'])
    assert r['coverage']['contact']['missing_rows']==1


def test_arithmetic_mismatch_flagged_without_overwriting_values(tmp_path):
    paths=inputs(tmp_path)
    base=pd.read_csv(paths['standard'],encoding='utf-8-sig');base.loc[0,'hit']=11
    base.to_csv(paths['standard'],index=False)
    out=tmp_path/'cleaned';r=clean_hitter_exports(paths,tmp_path/'raw',out)
    assert r['standard_checks']['hit_components']['flagged_rows']==1
    assert pd.read_parquet(out/'hitter_master.parquet').hit.tolist()==[11,10]
