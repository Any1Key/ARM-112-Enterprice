"""Per-attempt SIP recordings, with safe attribution of legacy run recordings."""
import os
from pathlib import Path
from sqlalchemy import select
from app.models import VoipCall,CardEvent

def media_root():return Path(os.getenv('CALL_MEDIA_ROOT','/media'))

def recording_files(s,run_id,calls=None):
    calls=calls if calls is not None else list(s.scalars(select(VoipCall).where(VoipCall.run_id==run_id).order_by(VoipCall.id)))
    root=media_root();result={}
    for call in calls:
        path=root/f'recording-{run_id}-{call.id}.wav'
        if path.is_file():result[call.id]=path
    modern={e.data.get('call_id') for e in s.scalars(select(CardEvent).where(CardEvent.run_id==run_id,CardEvent.kind=='sip.queued')) if e.data.get('recording_per_call')}
    legacy=[c for c in calls if c.answered_at and c.id not in modern]
    old=root/f'recording-{run_id}.wav'
    if legacy and old.is_file():result.setdefault(max(legacy,key=lambda c:c.id).id,old)
    return result
