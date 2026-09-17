"""Build a reproducible coverage report and review samples from the supplied EKP.

Run inside the app environment: python tools/audit_generation.py --output /tmp/audit.
--update-manifest deliberately updates the checked-in normative snapshot, only
after all workbook rows have a supported profile.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.classifier import FLAGS, parse_workbook, resolve_rules
from app.scenario_profiles import build_profile, case_contexts, manifest
from app.generation import generate_local
from app.scenario_locations import location_kind, METRO_STATIONS, MCK_STATIONS, AIRPORTS, RAIL_STATIONS, FREIGHT_STATIONS
from app.caller_variation import PLATE, REGIONS


def main():
    args=argparse.ArgumentParser()
    args.add_argument('--output',type=Path,default=Path('/tmp/ekp-generation-audit'))
    args.add_argument('--update-manifest',action='store_true')
    args=args.parse_args()
    data=parse_workbook(next(Path('source_materials').glob('*.xlsx')))
    snapshot={'source_sha256':data['sha256'],'items':[{k:x[k] for k in ('code','group_code','title','features')} for x in data['items']]}
    for t in snapshot['items']:
        p=build_profile(t)
        if len(set(p.cases))<2 or set(p.flags)-set(FLAGS):
            raise ValueError('Incomplete profile: '+t['code'])
    if args.update_manifest:
        Path('app/ekp_generation_manifest.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n')
        manifest.cache_clear()
    if manifest()!={t['code']:t for t in snapshot['items']}:
        raise ValueError('Workbook has changed; review profiles and update the manifest')
    args.output.mkdir(parents=True,exist_ok=True)
    samples=[]; timings=[]; calls=0; locations=Counter(); full_car_numbers=0
    for t in data['items']:
        p=build_profile(t)
        examples=[]
        for difficulty in ('basic','advanced','complex'):
            for variation in range(4):
                started=time.perf_counter()
                generated,meta=generate_local(t,{},difficulty)
                timings.append((time.perf_counter()-started)*1000)
                calls+=1
                if meta['attempts'] or meta['engine']!='scenario-template': raise ValueError('Unexpected model use '+t['code'])
                if not re.fullmatch(r'\+7 9\d{2} \d{3} \d{2} \d{2}',generated.aon): raise ValueError('Invalid AON '+t['code'])
                if generated.address not in generated.caller_text or generated.caller_name not in generated.caller_text: raise ValueError('Card/text mismatch '+t['code'])
                if 'Секунду, постараюсь объяснить.' in generated.caller_text or 'Сейчас могу передать только эти сведения.' in generated.caller_text: raise ValueError('Repeated padding '+t['code'])
                for plate in PLATE.finditer(generated.caller_text):
                    if plate[0] not in generated.expected_description or plate[4] not in REGIONS or not 1<=int(plate[2])<=999: raise ValueError('Invalid or inconsistent car number '+t['code'])
                    full_car_numbers+=1
                kind=location_kind(t,p.setting,generated.expected_description)
                locations[kind]+=1
                if kind in ('metro','mck','airport','rail','bus-station','port','forest','water','dam','road-tunnel','bridge','open-ground') and re.search(r'\bулица\b|\bдом\b|\bквартира\b',generated.address,re.I):
                    raise ValueError('Residential address for special object '+t['code'])
                if kind=='metro':
                    city=generated.address.split(',')[0][6:]
                    if city not in METRO_STATIONS or not any(station in generated.address for station in METRO_STATIONS[city]): raise ValueError('Invalid metro pair '+t['code'])
                elif kind=='mck':
                    if not generated.address.startswith('город Москва,') or not any(station in generated.address for station in MCK_STATIONS): raise ValueError('Invalid MCK pair '+t['code'])
                elif kind in ('airport','rail'):
                    pairs=AIRPORTS if kind=='airport' else RAIL_STATIONS+FREIGHT_STATIONS
                    if not any(generated.address.startswith(f'город {city}, ') and name in generated.address for city,name in pairs): raise ValueError('Invalid transport pair '+t['code'])
                if variation==0:
                    examples.append({'difficulty':difficulty,**generated.model_dump(),'services':list(dict.fromkeys(x['code'] for x in resolve_rules(t['rules'],generated.flags)))})
        samples.append({'code':t['code'],'title':t['title'],'group':t['category'],'profile':p.family,
                        'core_cases':list(p.cases),'details':list(p.details),'contexts':[{'text':s,'flags':f} for s,f in case_contexts(p)],'examples':examples})
    ordered=sorted(timings)
    summary={'classifier_sha256':data['sha256'],'types':len(samples),'groups':len(data['groups']),
             'profiles':len({x['profile'] for x in samples}),'generated_calls':calls,'model_requests':0,'template_version':9,'full_car_numbers':full_car_numbers,
             'generation_ms':{'median':round(ordered[len(ordered)//2],3),'p95':round(ordered[int(len(ordered)*.95)],3),'max':round(max(ordered),3)},
             'coverage_by_group':dict(Counter(x['category'] for x in data['items'])), 'location_checks':dict(locations)}
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    (args.output/'examples.json').write_text(json.dumps(samples,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
