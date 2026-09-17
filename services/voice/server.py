import hashlib, subprocess, tempfile, os, wave, threading, weakref
from pathlib import Path
from fastapi import FastAPI
from pydantic import BaseModel, Field
from piper import PiperVoice
from selection import choose_voice, DISABLED_VOICES
app=FastAPI();root=Path('/media');root.mkdir(exist_ok=True)
voice_models={}
for model_path in sorted(Path('/models').glob('ru_RU-*-medium.onnx')):
    if model_path.stem.removeprefix('ru_RU-').removesuffix('-medium') in DISABLED_VOICES:
        continue
    try: voice_models[model_path.stem.removeprefix('ru_RU-').removesuffix('-medium')]=PiperVoice.load(model_path)
    except Exception: pass
voice_lock=threading.Lock()
sound_locks=weakref.WeakValueDictionary()
sound_locks_guard=threading.Lock()
class Speech(BaseModel):
    text:str=Field(min_length=1,max_length=10000)
    voice:str|None=None
    caller_name:str=Field(default='',max_length=200)
@app.get('/health')
def health():return {'status':'ok','engine':'piper-russian' if voice_models else 'espeak-ng','language':'ru','voices':sorted(voice_models)}
@app.post('/speech')
def speech(data:Speech):
    voice_name=choose_voice(voice_models,data.caller_name,data.text,data.voice,stable_key=data.caller_name+"\0"+data.text)
    # Include the engine and voice revision so cached espeak files are never
    # reused after switching to neural Piper voices.
    key=hashlib.sha256((f'piper-{voice_name}-v1\\0'+data.text).encode()).hexdigest();target=root/(key+'.wav')
    with sound_locks_guard:
        sound_lock=sound_locks.get(key)
        if sound_lock is None:
            sound_lock=threading.Lock();sound_locks[key]=sound_lock
    with sound_lock:
        cached=target.exists()
        if not cached:
            with tempfile.TemporaryDirectory(dir=root) as temp:
                original=Path(temp)/'original.wav';converted=Path(temp)/'converted.wav'
                if voice_name in voice_models:
                    with voice_lock, wave.open(str(original),'wb') as wav:
                        voice_models[voice_name].synthesize_wav(data.text, wav)
                else:
                    subprocess.run(['espeak-ng','-v','ru+f3' if voice_name=='espeak-female' else 'ru','-s','145','-w',str(original),'--stdin'],input=data.text.encode(),check=True,timeout=60)
                subprocess.run(['sox',str(original),'-r','8000','-c','1','-b','16',str(converted)],check=True,timeout=30)
                os.replace(converted,target)
    return {'key':key,'sound':'/media/'+key,'engine':'piper-'+voice_name if voice_name in voice_models else 'espeak-ng','voice':voice_name,'cached':cached}
