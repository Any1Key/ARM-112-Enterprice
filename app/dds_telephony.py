"""Outbound DDS SIP calls, observed by Asterisk AMI rather than browser claims."""
import socket
import threading
import time
from datetime import datetime,timezone
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from sqlalchemy import select
from app.models import VoipCall, CardEvent, Audit, SipAccount, Lesson, SessionRun, RunContext
from app.workflows import get_run, ensure_writable, events_for
from app.dds import directory
from app.telephony import ami_action,ami_connect,read_frame,prepare_speech,phone_registered,launch_worker
from app.service_directory import spoken_entries

class CallIn(BaseModel):
    service:str=Field(default='',max_length=100)
    role:Literal['brigade','superior','service']|None=None


def register_dds_telephony(app,db,current,factory):
    def record(call_id,state,channel=None,error=None):
        with factory() as s:
            call=s.scalar(select(VoipCall).where(VoipCall.id==call_id).with_for_update())
            if not call or call.state in ('ended','failed','cancelled'):return
            run=s.get(SessionRun,call.run_id)
            if run.finished_at:
                call.state='cancelled';call.ended_at=datetime.now(timezone.utc)
            else:
                call.state=state
                if channel:call.channel=channel
                if state=='answered':call.answered_at=datetime.now(timezone.utc)
                if state in ('ended','failed'):call.ended_at=datetime.now(timezone.utc)
                if error:call.error=error
                s.add(CardEvent(run_id=call.run_id,user_id=run.student_id,kind='sip.'+state,data={'call_id':call.id,'direction':'outbound','error':error}))
            s.commit()

    def monitor(call_id,run_id,user_id,extension,ready):
        sock=stream=None
        deadline=time.monotonic()+90
        channel=None
        try:
            sock,stream=ami_connect();sock.settimeout(3);ready.set()
            # File streams cannot resume after socket timeout; use AMI events plus
            # keepalive frames from Asterisk (periodic request) on a dedicated socket.
            sock.settimeout(650)
            timer_stop=threading.Event()
            def heartbeat():
                while not timer_stop.wait(2):
                    try:sock.sendall(b'Action: Ping\r\nActionID: dds-ping\r\n\r\n')
                    except OSError:return
            threading.Thread(target=heartbeat,daemon=True).start()
            while time.monotonic()<deadline:
                event=read_frame(stream)
                with factory() as s:
                    call=s.get(VoipCall,call_id);run=s.get(SessionRun,run_id);context=s.get(RunContext,run_id)
                    lesson=s.get(Lesson,context.lesson_id) if context and context.lesson_id else None
                    cancelled=not call or call.state=='cancelled' or not run or bool(run.finished_at) or not lesson or lesson.status!='active'
                if event.get('Event')=='UserEvent' and event.get('UserEvent')=='DDSAnswered' and event.get('CallID')==str(call_id) and event.get('UserID')==str(user_id):channel=event.get('Channel')
                if cancelled:
                    if channel:
                        try:ami_action('Hangup',Channel=channel)
                        except (OSError,ConnectionError):pass
                    return
                if event.get('Event')=='UserEvent' and event.get('UserEvent')=='DDSAnswered' and event.get('CallID')==str(call_id) and event.get('UserID')==str(user_id):
                    channel=event.get('Channel');record(call_id,'answered',channel=channel);deadline=time.monotonic()+600
                if event.get('Event')=='Hangup' and channel and event.get('Channel')==channel:
                    record(call_id,'ended');return
            if channel:
                try:ami_action('Hangup',Channel=channel)
                except (OSError,ConnectionError):pass
            record(call_id,'failed',error='Время ожидания учебного SIP-вызова истекло')
        except Exception:
            record(call_id,'failed',error='Соединение с Asterisk прервано. Повторите вызов.');ready.set()
        finally:
            if 'timer_stop' in locals():timer_stop.set()
            try:ami_action('Command',Command=f'database del dds {user_id}/{extension}')
            except (OSError,ConnectionError):pass
            if stream:stream.close()
            if sock:sock.close()

    @app.post('/api/dds/runs/{run_id}/sip-call')
    def prepare(run_id:int,x:CallIn,u=Depends(current),s=Depends(db)):
        if u.role!='student':raise HTTPException(403)
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        if not context or context.scenario_snapshot.get('mode')!='dds':raise HTTPException(422,'Не карточка ДДС')
        status_mode=context.scenario_snapshot.get('dds_workflow')=='status'
        if status_mode:
            if not x.role:raise HTTPException(422,'Выберите собеседника для учебного звонка')
            if x.role=='service':
                service=x.service
                if not service or service not in run.answers.get('services',[]) or service==context.scenario_snapshot['service_code']:
                    raise HTTPException(422,'Выберите другую службу, получившую эту карточку')
                phone=run.answers.get('service_phones',{}).get(service,'').strip()
                name=directory(s).get(service,service)
                greeting=f'{name}. Дежурный учебной службы слушает. Сообщите обстоятельства и необходимые совместные действия.'
            else:
                service=context.scenario_snapshot['service_code']
                if x.service and x.service!=service:raise HTTPException(403,'Звонок относится только к своей учебной службе')
                phone=''
                name='Руководитель реагирующей бригады' if x.role=='brigade' else 'Вышестоящий начальник ДДС'
                greeting=f'{name}. Учебная линия слушает. Сообщите обстоятельства и необходимые действия.'
        else:
            if not any(e.kind=='dds.validation' for e in events_for(s,run)):raise HTTPException(409,'Сначала сохраните проверку карточки')
            if x.service not in run.answers['services']:raise HTTPException(422,'Служба отсутствует в проверенной карточке')
            service=x.service;name=directory(s).get(service,service);phone=''
            greeting=f'{name}. Дежурный учебной службы слушает. Передайте место происшествия, обстоятельства и необходимые меры.'
        account=s.get(SipAccount,u.id)
        if not account:raise HTTPException(409,'Подключите браузерный или аппаратный SIP-телефон')
        active=s.scalar(select(VoipCall).where(VoipCall.run_id==run.id,VoipCall.state.in_(['queued','ringing','answered'])))
        if active:raise HTTPException(409,'Завершите предыдущий звонок или отмените его')
        try:
            if not phone_registered(account.username) and not phone_registered(account.username+'-hw'):raise HTTPException(409,'SIP-телефон не зарегистрирован')
            speech=prepare_speech(greeting,'Диспетчер Алексей')
            call=VoipCall(run_id=run.id,state='ringing',sound_key=speech['key']);s.add(call);s.flush()
            extension='80'+str(call.id)
            ami_action('Command',Command=f'database put dds {u.id}/{extension} {speech["key"]}:{run.id}:{call.id}')
            service_number=next((item['extension'] for item in spoken_entries(directory(s)) if item['code']==service),service)
            s.add(CardEvent(run_id=run.id,user_id=u.id,kind='dds.call.prepared',data={'call_id':call.id,'extension':extension,'service':service,'name':name,'service_number':service_number,'role':x.role if status_mode else None,'phone':phone,'direction':'outbound'}))
            s.add(CardEvent(run_id=run.id,user_id=u.id,kind='sip.audio_ready',data={'call_id':call.id,'voice':speech.get('voice'),'engine':speech.get('engine'),'cached':speech.get('cached',False),'preparation_ms':0}))
            s.add(Audit(user_id=u.id,action='dds.call.prepared',details={'run_id':run.id,'call_id':call.id,'service':service,'role':x.role if status_mode else None}));s.commit()
        except HTTPException:raise
        except Exception as exc:
            s.rollback();raise HTTPException(503,'Учебная SIP-служба недоступна. Проверьте подключение телефонии.') from exc
        ready=threading.Event();launch_worker(monitor,(call.id,run.id,u.id,extension,ready))
        if not ready.wait(5):
            record(call.id,'failed',error='Не удалось подключить наблюдение за SIP-вызовом')
            raise HTTPException(503,'Не удалось подготовить SIP-вызов')
        s.refresh(call)
        if call.state=='failed':raise HTTPException(503,'Asterisk недоступен')
        service_number=next((item['extension'] for item in spoken_entries(directory(s)) if item['code']==service),service)
        return {'call_id':call.id,'extension':extension,'service':service,'service_number':service_number,'name':name,'role':x.role if status_mode else None,'phone':phone,'training':True}
