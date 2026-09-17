"""Actual Asterisk AMI calls and offline synthesized audio, tied to student sessions."""
import os, socket, secrets, threading, time, hmac, hashlib, base64
from pathlib import Path
from datetime import datetime,timezone
import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from app.models import SipAccount, VoipCall, SessionRun, RunContext, AccountState, Audit, CardEvent
from app.workflows import get_run, ensure_writable
from app.speech_text import tts_text
provision_lock=threading.Lock()

def frame(data):
    return ''.join(f'{key}: {value}\r\n' for key,value in data.items())+'\r\n'
def read_frame(stream):
    result={}
    while True:
        line=stream.readline()
        if not line:raise ConnectionError('AMI closed')
        line=line.decode().strip()
        if not line:
            if result:return result
            continue
        if ': ' in line:
            key,value=line.split(': ',1);result[key]=value

def ami_connect():
    sock=socket.create_connection((os.getenv('ASTERISK_HOST','asterisk'),5038),timeout=5);stream=sock.makefile('rb');stream.readline()
    sock.sendall(frame({'Action':'Login','Username':'arm112','Secret':os.getenv('ASTERISK_AMI_SECRET','arm112-local-ami-change-me'),'Events':'on'}).encode())
    while True:
        reply=read_frame(stream)
        if reply.get('Response'):
            if reply['Response']!='Success':sock.close();raise ConnectionError('AMI login failed')
            return sock,stream

def ami_action(action,**fields):
    sock,stream=ami_connect()
    try:
        action_id=secrets.token_hex(12);sock.sendall(frame({'Action':action,'ActionID':action_id,**fields}).encode())
        while True:
            reply=read_frame(stream)
            if reply.get('ActionID')==action_id and reply.get('Response'):
                if reply['Response']=='Error':raise ConnectionError(reply.get('Message','AMI error'))
                return reply
    finally:stream.close();sock.close()

def write_accounts(s):
    blocked={x.user_id for x in s.scalars(select(AccountState).where(AccountState.blocked.is_(True)))}
    text='; Generated locally by ARM112; no external endpoints\n'
    for account in s.scalars(select(SipAccount)):
        if account.user_id in blocked:continue
        name=account.username
        text+=f'''[{name}]
type=endpoint
transport=transport-ws
from_domain=arm112.local
context=training
disallow=all
allow=ulaw,alaw
auth={name}-auth
aors={name}
webrtc=yes
media_encryption=dtls
dtls_auto_generate_cert=yes
use_avpf=yes
ice_support=yes
rtcp_mux=yes
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
direct_media=no
[{name}-auth]
type=auth
auth_type=userpass
username={name}
password={account.password}
[{name}]
type=aor
max_contacts=1
remove_existing=yes
qualify_frequency=30
'''
    root=Path(os.getenv('SIP_PROVISION_ROOT','/provision'));root.mkdir(exist_ok=True);temp=root/'users.conf.tmp';temp.write_text(text);temp.chmod(0o600);temp.replace(root/'users.conf')
    ami_action('Command',Command='pjsip reload')

def register_telephony(app,db,current,session_factory):
    def permitted(s,u,run_id):
        run,context=get_run(s,u,run_id)
        return run,context
    def update(call_id,state,**values):
        with session_factory() as s:
            call=s.get(VoipCall,call_id)
            if not call or call.state=='cancelled':return
            call.state=state
            for key,value in values.items():setattr(call,key,value)
            run=s.get(SessionRun,call.run_id)
            s.add(CardEvent(run_id=run.id,user_id=run.student_id,kind='sip.'+state,data={'call_id':call.id,**{k:str(v) for k,v in values.items()}}))
            s.add(Audit(user_id=run.student_id,action='sip.'+state,details={'call_id':call.id,'run_id':run.id}))
            context=s.get(RunContext,run.id)
            if state=='answered' and not run.finished_at and not run.answers and not (context and context.scenario_snapshot.get('timer_active_since')):
                run.started_at=values['answered_at']
            s.commit()
    def cancelled(call_id):
        with session_factory() as s:
            call=s.get(VoipCall,call_id)
            return not call or call.state=='cancelled' or bool(s.get(SessionRun,call.run_id).finished_at)
    def worker(call_id,account,run_id,text,caller_name):
        sock=None;stream=None
        try:
            with httpx.Client(timeout=60,trust_env=False) as client:
                response=client.post(os.getenv('VOICE_URL','http://voice:8092')+'/speech',json={'text':tts_text(text),'caller_name':caller_name});response.raise_for_status()
                speech=response.json();sound=speech['key']
            if cancelled(call_id):return
            sock,stream=ami_connect();sock.settimeout(45)
            action_id=f'arm112-{call_id}'
            sock.sendall(frame({'Action':'Originate','ActionID':action_id,'Channel':'PJSIP/'+account,'Context':'training-playback','Exten':'s','Priority':1,'CallerID':'Учебный абонент <112>','Timeout':30000,'Async':'true','Variable':f'ARM_SOUND={sound},ARM_RUN_ID={run_id}'}).encode())
            update(call_id,'ringing',sound_key=sound);channel=None;deadline=time.monotonic()+600
            while time.monotonic()<deadline:
                reply=read_frame(stream)
                if reply.get('ActionID')==action_id and reply.get('Response')=='Error':raise ConnectionError(reply.get('Message','Originate failed'))
                if reply.get('Event')=='OriginateResponse' and reply.get('ActionID')==action_id:
                    if reply.get('Response')!='Success':raise ConnectionError('Абонент не ответил: '+reply.get('Reason','unknown'))
                    channel=reply.get('Channel')
                    if cancelled(call_id):
                        if channel:ami_action('Hangup',Channel=channel)
                        return
                    update(call_id,'answered',channel=channel,answered_at=datetime.now(timezone.utc));sock.settimeout(120)
                if reply.get('Event')=='Hangup' and channel and reply.get('Channel')==channel:
                    update(call_id,'ended',ended_at=datetime.now(timezone.utc));return
            raise TimeoutError('Call timeout')
        except Exception as exc:update(call_id,'failed',error=str(exc),ended_at=datetime.now(timezone.utc))
        finally:
            if stream:stream.close()
            if sock:sock.close()
    @app.get('/api/telephony/health')
    def health(u=Depends(current)):
        try:ami_action('Ping');return {'status':'ok','transport':'SIP/WebRTC','voice':'espeak-ng'}
        except (OSError,ConnectionError):return {'status':'unavailable','message':'Профиль voip не запущен или Asterisk недоступен'}
    @app.post('/api/telephony/account')
    def account(request:Request,u=Depends(current),s=Depends(db)):
        if u.role!='student':raise HTTPException(403)
        account=s.get(SipAccount,u.id)
        if not account:account=SipAccount(user_id=u.id,username='arm'+str(u.id),password=secrets.token_hex(24));s.add(account);s.flush()
        try:
            with provision_lock:write_accounts(s)
        except (OSError,ConnectionError) as exc:raise HTTPException(503,'Asterisk недоступен') from exc
        s.add(Audit(user_id=u.id,action='sip.account',details={'username':account.username}));s.commit()
        turn_username=str(int(time.time())+3600)+':'+str(u.id)
        return {'username':account.username,'password':account.password,'domain':request.url.hostname,'ws_path':'/sip-ws','ws_port':8088,'sip_port':5060,'ice_servers':[{'urls':'turn:'+request.url.hostname+':3478?transport=tcp','username':turn_username,'credential':base64.b64encode(hmac.new(os.getenv('TURN_SECRET','arm112-local-turn-change-me').encode(),turn_username.encode(),hashlib.sha1).digest()).decode()}]}
    @app.post('/api/telephony/runs/{run_id}/call')
    def call(run_id:int,u=Depends(current),s=Depends(db)):
        run,context=permitted(s,u,run_id)
        if u.role!='student' or run.student_id!=u.id:raise HTTPException(403)
        ensure_writable(run,context,s)
        if context and context.scenario_snapshot.get('mode')=='dispatch':raise HTTPException(409,'ДДС получает карточки; голосовой вызов относится к режиму 112')
        existing=s.scalar(select(VoipCall).where(VoipCall.run_id==run.id,VoipCall.state.in_(['queued','ringing','answered'])))
        if existing:return {'call_id':existing.id,'state':existing.state}
        account=s.get(SipAccount,u.id)
        if not account:raise HTTPException(409,'Сначала подключите учебный телефон')
        text=context.scenario_snapshot['caller_text']
        caller_name=context.scenario_snapshot.get('expected',{}).get('caller_name','') or ''
        # Keep this key in sync with voice-service cache versioning.
        new=VoipCall(run_id=run.id,sound_key='pending');s.add(new);s.commit()
        threading.Thread(target=worker,args=(new.id,account.username,run.id,text,caller_name),daemon=True).start()
        return {'call_id':new.id,'state':new.state}
    @app.get('/api/telephony/runs/{run_id}')
    def calls(run_id:int,u=Depends(current),s=Depends(db)):
        permitted(s,u,run_id)
        return [{'id':x.id,'state':x.state,'answered_at':x.answered_at,'ended_at':x.ended_at,'error':x.error} for x in s.scalars(select(VoipCall).where(VoipCall.run_id==run_id).order_by(VoipCall.id))]
    @app.get('/api/telephony/runs/{run_id}/recording')
    def recording(run_id:int,u=Depends(current),s=Depends(db)):
        permitted(s,u,run_id)
        path=Path('/media')/f'recording-{run_id}.wav'
        if not path.exists():raise HTTPException(404,'Запись ещё не создана')
        if s.scalar(select(VoipCall.id).where(VoipCall.run_id==run_id,VoipCall.state.in_(['queued','ringing','answered']))):raise HTTPException(409,'Запись ещё идёт')
        return FileResponse(path,media_type='audio/wav')

def recover_interrupted_calls(s):
    # Origination workers belong to this process. After restart their calls need
    # a terminal state, otherwise a retry would return a stale active call forever.
    for call in s.scalars(select(VoipCall).where(VoipCall.state.in_(['queued','ringing','answered']))):
        if call.channel:
            try:ami_action('Hangup',Channel=call.channel)
            except (OSError,ConnectionError):pass
        call.state='failed';call.error='Вызов прерван перезапуском сервиса';call.ended_at=datetime.now(timezone.utc)
        s.add(Audit(action='sip.recovered',details={'call_id':call.id,'run_id':call.run_id}))
    s.commit()
