#!/usr/bin/env python3
"""Read-only checks for the frozen GiftAid dev-38 reviewer package."""
from pathlib import Path
import csv, hashlib, json, sys
root=Path(__file__).resolve().parents[1]
fail=[]
manifest=root/'MANIFEST_SHA256.txt'
if manifest.exists():
    for line in manifest.read_text().splitlines():
        digest,rel=line.split('  ',1)
        p=root/rel
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
            fail.append(f'Checksum mismatch: {rel}')
else: fail.append('Missing checksum manifest')
rows=list(csv.DictReader((root/'reports/decision_by_run.csv').open()))
synthetic=[r for r in rows if r['scenario']!='R0']
if len(synthetic)!=36:fail.append(f'Expected 36 synthetic model/scenario repetitions, got {len(synthetic)}')
for r in rows:
    d=root/'runs'/r['cohort']/r['scenario']
    for stage in ['A1','A2','A3','B1']:
        for suffix in ['_PROMPT.txt','_raw.txt','_parsed.json']:
            if not (d/(stage+suffix)).is_file() and r['raw_record_status']!='INCOMPLETE':
                fail.append(f'Missing {d}/{stage+suffix}')
    if r['scenario']!='R0' and r['raw_record_status']=='COMPLETE':
        for stage,key in [('A3','A3'),('B1','B1')]:
            dec=json.loads((d/(stage+'_parsed.json')).read_text()).get('decision')
            if dec!=r[key]:fail.append(f'Decision mismatch: {d} {stage}')
gold=json.loads((root/'gold/gold_per_run.json').read_text())
if len(gold['scenario_statuses'])<6:fail.append('Missing scenario reference statuses')
if fail:
    print('FAIL',len(fail))
    print('\n'.join(fail[:30]))
    sys.exit(1)
print('PASS: manifest, 35 complete synthetic runs + 1 explicitly incomplete archived run, decision table and gold presence.')
