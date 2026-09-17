"""A burst of identical calls must synthesize one file, not queue duplicate inference."""
import importlib.util
import sys
import time
import types
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from services.voice import selection


def test_simultaneous_identical_voice_requests_share_one_synthesis(monkeypatch,tmp_path):
    monkeypatch.setitem(sys.modules,'selection',selection)
    monkeypatch.setitem(sys.modules,'piper',types.SimpleNamespace(PiperVoice=types.SimpleNamespace(load=lambda p:None)))
    spec=importlib.util.spec_from_file_location('isolated_voice_server',Path('services/voice/server.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.root=tmp_path;module.voice_models={};calls=[]
    def fake_run(args,**kwargs):
        calls.append(args[0]);time.sleep(.02)
        if args[0]=='espeak-ng':Path(args[args.index('-w')+1]).write_bytes(b'audio')
        else:Path(args[-1]).write_bytes(b'converted-audio')
    monkeypatch.setattr(module.subprocess,'run',fake_run)
    message=module.Speech(text='Тестовая реплика',caller_name='Анна Орлова')
    with ThreadPoolExecutor(max_workers=20) as pool:result=list(pool.map(lambda _:module.speech(message),range(20)))
    assert len({x['key'] for x in result})==1
    assert calls.count('espeak-ng')==1 and calls.count('sox')==1
    assert (tmp_path/(result[0]['key']+'.wav')).read_bytes()==b'converted-audio'
    module.speech(message)
    assert len(calls)==2
