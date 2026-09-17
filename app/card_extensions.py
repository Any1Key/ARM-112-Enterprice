"""Audited local training workflows: supplements, links, reminders and SMS."""
from datetime import timedelta, datetime
from pathlib import Path
import secrets
import threading
from collections import OrderedDict
from fastapi import Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field,ValidationError
from sqlalchemy import select
from app.models import SessionRun,RunContext,IncidentType,ClassifierVersion,CardEvent,OperatorPresence,TrainingMessage,TrainingIssue,Scenario,User
from app.schemas import CardIn,SupplementIn
from app.classifier import SERVICE_NAMES
from app.workflows import get_run,ensure_writable,run_payload,now,aware,log,begin_run,check_scenario_access
from app.questionnaires import catalog

class PresenceIn(BaseModel):
    state:str=Field(pattern='^(available|unavailable|disconnected|error)$')
class LinkIn(BaseModel):
    parent_id:int=Field(ge=1)
class ReminderIn(BaseModel):
    text:str=Field(min_length=1,max_length=1000)
    at:datetime
class SmsIn(BaseModel):
    text:str=Field(min_length=1,max_length=2000)
class IncomingSmsIn(SmsIn):
    student_id:int=Field(ge=1)
    scenario_id:int=Field(ge=1)
    aon:str=Field(min_length=1,max_length=100)
    latitude:float|None=Field(default=None,ge=-90,le=90)
    longitude:float|None=Field(default=None,ge=-180,le=180)

catalog_cache=OrderedDict()
catalog_lock=threading.RLock()

SUPPLEMENT_FIELDS={'description','operator_comment','address','caller_phone','on_site_phone','country','region','city','district','borough','street','house','building','structure','apartment','entrance','floor','access_code','descriptive_address','latitude','longitude','object_name','victims','victims_count'}
MUTABLE={'description','operator_comment','victims','victims_count'}

def supplement_allowed(card):return [x for x in SUPPLEMENT_FIELDS if x in MUTABLE or card.get(x) in (None,'')]
def snapshot_update(context,**changes):context.scenario_snapshot={**context.scenario_snapshot,**changes}
def event(s,u,run,kind,data):s.add(CardEvent(run_id=run.id,user_id=u.id,kind=kind,data=data));log(s,u,kind,{'run_id':run.id,**data})
def presence(s,u):
    row=s.get(OperatorPresence,u.id)
    if not row:row=OperatorPresence(user_id=u.id,state='unavailable');s.add(row)
    return row

def register_extensions(app,db,current):
    @app.get('/api/questionnaires')
    def questionnaires(u=Depends(current),s=Depends(db)):
        version=s.scalar(select(ClassifierVersion).order_by(ClassifierVersion.id.desc()))
        if not version:return {'version':'2026-09-17.1','cards':[]}
        key=(str(s.bind.url),version.id,version.sha256)
        with catalog_lock:
            if key not in catalog_cache:
                types=list(s.scalars(select(IncidentType).where(IncidentType.version_id==version.id)))
                catalog_cache[key]={'version':'2026-09-17.1','cards':catalog([{**t.data,'id':t.id} for t in types],version.manifest['groups'])}
                while len(catalog_cache)>8:catalog_cache.popitem(last=False)
            return catalog_cache[key]

    @app.get('/api/services/directory')
    def directory(u=Depends(current),s=Depends(db)):
        services=dict(SERVICE_NAMES)
        version=s.scalar(select(ClassifierVersion).order_by(ClassifierVersion.id.desc()))
        if version:
            for c in version.manifest['columns']:services.setdefault(c['code'],c['name'])
        return [{'code':code,'name':name,'phone':code if code in ('101','102','103','104') else '', 'extension':code if code in ('101','102','103','104') else '900'} for code,name in services.items() if code!='MOSBEZ_ANALYTICS']

    @app.get('/api/operator/presence')
    def read_presence(u=Depends(current),s=Depends(db)):
        row=presence(s,u)
        active=s.scalar(select(SessionRun.id).where(SessionRun.student_id==u.id,SessionRun.finished_at.is_(None)))
        pending=row.available_after and aware(row.available_after)>now()
        value='unavailable' if active or pending else row.state
        s.commit();return {'state':value,'requested_state':row.state,'available_after':row.available_after,'active_run':active}

    @app.put('/api/operator/presence')
    def set_presence(x:PresenceIn,u=Depends(current),s=Depends(db)):
        if u.role!='student':raise HTTPException(403)
        row=presence(s,u);row.state=x.state;row.updated_at=now();log(s,u,'operator.presence',x.model_dump());s.commit();return {'state':row.state}

    @app.post('/api/runs/{run_id}/supplement/lock')
    def lock(run_id:int,u=Depends(current),s=Depends(db)):
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        if not context or not context.registered_at:raise HTTPException(409,'Сначала сохраните карточку')
        if context.scenario_snapshot.get('mode')=='dispatch':raise HTTPException(403,'Исходную карточку дополняет оператор 112')
        previous=context.scenario_snapshot.get('edit_lock',{})
        if previous and datetime.fromisoformat(previous['until'])>now():raise HTTPException(409,'Карточка уже дополняется в другой вкладке')
        token=secrets.token_hex(24);snapshot_update(context,edit_lock={'user_id':u.id,'token':token,'until':(now()+timedelta(minutes=5)).isoformat()})
        s.commit();return {'token':token,'revision':context.revision,'fields':supplement_allowed(run.answers),'card':run.answers}

    @app.delete('/api/runs/{run_id}/supplement/lock')
    def unlock(run_id:int,token:str,u=Depends(current),s=Depends(db)):
        run,context=get_run(s,u,run_id,True)
        existing=context.scenario_snapshot.get('edit_lock',{})
        if existing.get('user_id')!=u.id or existing.get('token')!=token:raise HTTPException(409,'Блокировка уже изменена')
        snapshot_update(context,edit_lock={});s.commit();return {'status':'ok'}

    @app.put('/api/runs/{run_id}/supplement')
    def supplement(run_id:int,x:SupplementIn,token:str,u=Depends(current),s=Depends(db)):
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        existing=context.scenario_snapshot.get('edit_lock',{}) if context else {}
        if not context or not context.registered_at or existing.get('user_id')!=u.id or existing.get('token')!=token or datetime.fromisoformat(existing['until'])<=now():raise HTTPException(409,'Получите блокировку редактирования заново')
        if x.revision!=context.revision:raise HTTPException(409,'Карточка изменилась; обновите данные')
        if not x.fields or set(x.fields)-set(supplement_allowed(run.answers)):raise HTTPException(422,'Можно дополнить только пустые поля, описание и сведения о пострадавших')
        try:checked=CardIn.model_validate({**run.answers,**x.fields}).model_dump()
        except ValidationError as exc:raise HTTPException(422,'Проверьте значения дополнения') from exc
        before={k:run.answers.get(k) for k in x.fields}
        run.answers={**run.answers,**{k:checked[k] for k in x.fields}}
        if 'victims' in x.fields or 'victims_count' in x.fields:
            victims=checked['victims_count']>0 if checked['victims_count'] is not None else checked['victims']
            run.answers={**run.answers,'victims':victims,'flags':{**run.answers.get('flags',{}),'victims':victims}}
        context.revision+=1;snapshot_update(context,edit_lock={})
        event(s,u,run,'card.supplement',{'before':before,'after':{k:checked[k] for k in x.fields},'revision':context.revision});s.commit();return run_payload(s,run,context)

    @app.get('/api/runs/{run_id}/matches')
    def matches(run_id:int,u=Depends(current),s=Depends(db)):
        run,context=get_run(s,u,run_id);phones={''.join(filter(str.isdigit,str(run.answers.get(k,'')))) for k in ('aon','caller_phone','on_site_phone')}-{''}
        address=run.answers.get('address','').strip().casefold();found=[]
        candidates=s.scalars(select(SessionRun).where(SessionRun.id!=run.id,SessionRun.started_at>=now()-timedelta(days=7)).order_by(SessionRun.id.desc()).limit(500))
        for other in candidates:
            try:get_run(s,u,other.id)
            except HTTPException:continue
            nums={''.join(filter(str.isdigit,str(other.answers.get(k,'')))) for k in ('aon','caller_phone','on_site_phone')}-{''}
            by_phone=bool(phones&nums);by_address=bool(address and address==other.answers.get('address','').strip().casefold())
            if by_phone or by_address:found.append({'run_id':other.id,'address':other.answers.get('address',''),'description':other.answers.get('description',''),'reason':'Телефон' if by_phone else 'Адрес','registered':bool((s.get(RunContext,other.id) and s.get(RunContext,other.id).registered_at))})
        return found

    @app.post('/api/runs/{run_id}/link')
    def link(run_id:int,x:LinkIn,u=Depends(current),s=Depends(db)):
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        parent,parent_context=get_run(s,u,x.parent_id,True)
        if not context.registered_at or not parent_context or not parent_context.registered_at:raise HTTPException(409,'Связи создаются между сохранёнными карточками')
        seen={run.id};cursor=parent_context
        while cursor:
            if cursor.run_id in seen:raise HTTPException(409,'Циклическая связь карточек недопустима')
            seen.add(cursor.run_id);next_id=cursor.scenario_snapshot.get('parent_run_id');cursor=s.get(RunContext,next_id) if next_id else None
        snapshot_update(context,parent_run_id=parent.id);event(s,u,run,'card.link',{'parent_run_id':parent.id});s.commit();return {'parent_run_id':parent.id}

    @app.post('/api/runs/{run_id}/reminder')
    def reminder(run_id:int,x:ReminderIn,u=Depends(current),s=Depends(db)):
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        at=aware(x.at)
        if at<=now():raise HTTPException(422,'Время напоминания должно быть в будущем')
        data={'text':x.text,'at':at.isoformat(),'user_id':u.id};snapshot_update(context,reminder=data);event(s,u,run,'card.reminder',data);s.commit();return data

    @app.get('/api/reminders')
    def due_reminders(u=Depends(current),s=Depends(db)):
        result=[]
        for context in s.scalars(select(RunContext)):
            item=context.scenario_snapshot.get('reminder')
            if not item or item['user_id']!=u.id or datetime.fromisoformat(item['at'])>now():continue
            run=s.get(SessionRun,context.run_id)
            if (run.report or {}).get('skipped'):continue
            result.append({'run_id':run.id,**item})
        return result

    @app.delete('/api/runs/{run_id}/reminder')
    def delete_reminder(run_id:int,u=Depends(current),s=Depends(db)):
        run,context=get_run(s,u,run_id,True)
        if context.scenario_snapshot.get('reminder',{}).get('user_id')!=u.id:raise HTTPException(404)
        snapshot_update(context,reminder=None);event(s,u,run,'card.reminder.delete',{});s.commit();return {'status':'ok'}

    @app.post('/api/runs/{run_id}/help')
    def help_request(run_id:int,x:SmsIn,u=Depends(current),s=Depends(db)):
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        event(s,u,run,'card.help',{'text':x.text,'operator':u.username});s.commit();return {'status':'ok'}

    @app.post('/api/sms/incoming')
    def enqueue_sms(x:IncomingSmsIn,u=Depends(current),s=Depends(db)):
        if u.role not in ('teacher','admin'):raise HTTPException(403)
        student=s.get(User,x.student_id);scenario=s.get(Scenario,x.scenario_id)
        if not student or student.role!='student' or not scenario or (u.role=='teacher' and scenario.created_by!=u.id):raise HTTPException(404)
        check_scenario_access(s,student,scenario)
        coordinates={k:getattr(x,k) for k in ('latitude','longitude') if getattr(x,k) is not None}
        # Further messages for the same caller go to the still-open SMS card.
        previous=s.scalar(select(TrainingMessage).where(TrainingMessage.student_id==student.id,TrainingMessage.aon==x.aon,TrainingMessage.run_id.is_not(None)).order_by(TrainingMessage.id.desc()))
        run=s.get(SessionRun,previous.run_id) if previous else None
        row=TrainingMessage(student_id=student.id,teacher_id=u.id,scenario_id=scenario.id,aon=x.aon,text=x.text,coordinates=coordinates,run_id=run.id if run and not run.finished_at else None);s.add(row);s.flush()
        if row.run_id:event(s,u,run,'sms.incoming',{'message_id':row.id,'text':row.text,'aon':row.aon})
        log(s,u,'sms.enqueue',{'message_id':row.id,'student_id':student.id});s.commit();return {'id':row.id,'run_id':row.run_id}

    @app.get('/api/sms/queue')
    def sms_queue(u=Depends(current),s=Depends(db)):
        if u.role!='student':raise HTTPException(403)
        row=presence(s,u)
        active=s.scalar(select(SessionRun.id).where(SessionRun.student_id==u.id,SessionRun.finished_at.is_(None)))
        if row.state!='available' or active or (row.available_after and aware(row.available_after)>now()):return []
        return [{'id':m.id,'aon':m.aon,'text':m.text,'at':m.created_at} for m in s.scalars(select(TrainingMessage).where(TrainingMessage.student_id==u.id,TrainingMessage.run_id.is_(None)).order_by(TrainingMessage.id))]

    @app.post('/api/sms/{message_id}/accept')
    def accept_sms(message_id:int,u=Depends(current),s=Depends(db)):
        if u.role!='student':raise HTTPException(403)
        s.scalar(select(User).where(User.id==u.id).with_for_update())
        message=s.scalar(select(TrainingMessage).where(TrainingMessage.id==message_id).with_for_update())
        if not message or message.student_id!=u.id:raise HTTPException(404)
        if message.run_id:return run_payload(s,*get_run(s,u,message.run_id))
        if s.scalar(select(SessionRun.id).where(SessionRun.student_id==u.id,SessionRun.finished_at.is_(None))):raise HTTPException(409,'Сначала завершите текущую карточку')
        row=presence(s,u)
        if row.state!='available' or (row.available_after and aware(row.available_after)>now()):raise HTTPException(409,'Оператор недоступен')
        run,context=begin_run(s,u,s.get(Scenario,message.scenario_id));run.answers={**run.answers,'aon':message.aon,'description':message.text,'channel':'SMS',**message.coordinates}
        for item in s.scalars(select(TrainingMessage).where(TrainingMessage.student_id==u.id,TrainingMessage.aon==message.aon,TrainingMessage.run_id.is_(None)).with_for_update()):
            item.run_id=run.id;event(s,u,run,'sms.incoming',{'message_id':item.id,'text':item.text,'aon':item.aon})
        snapshot_update(context,sms_card=True);s.commit();return run_payload(s,run,context)

    @app.get('/api/runs/{run_id}/sms')
    def messages(run_id:int,u=Depends(current),s=Depends(db)):
        run,context=get_run(s,u,run_id)
        for item in s.scalars(select(TrainingMessage).where(TrainingMessage.run_id==run.id)):
            if u.id==run.student_id:item.read=True
        s.commit();return [{'at':e.at,'direction':'incoming' if e.kind=='sms.incoming' else 'outgoing',**e.data} for e in s.scalars(select(CardEvent).where(CardEvent.run_id==run.id,CardEvent.kind.in_(['sms.incoming','sms.outgoing'])).order_by(CardEvent.id))]

    @app.post('/api/runs/{run_id}/sms')
    def reply(run_id:int,x:SmsIn,u=Depends(current),s=Depends(db)):
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        if u.role!='student':raise HTTPException(403)
        if not run.answers.get('aon'):raise HTTPException(422,'Неизвестен АОН заявителя')
        event(s,u,run,'sms.outgoing',{'text':x.text,'aon':run.answers['aon'],'training':True});s.commit();return {'status':'sent','training':True}

    @app.get('/api/notifications')
    def notifications(u=Depends(current),s=Depends(db)):
        if u.role not in ('teacher','admin'):return []
        result=[]
        for e in s.scalars(select(CardEvent).where(CardEvent.kind=='card.help',CardEvent.at>=now()-timedelta(days=1)).order_by(CardEvent.id.desc()).limit(200)):
            try:run,context=get_run(s,u,e.run_id)
            except HTTPException:continue
            if not e.data.get('acknowledged_by'):result.append({'id':e.id,'run_id':run.id,'at':e.at,**e.data})
        return result

    @app.post('/api/notifications/{event_id}/ack')
    def acknowledge(event_id:int,u=Depends(current),s=Depends(db)):
        if u.role not in ('teacher','admin'):raise HTTPException(403)
        e=s.get(CardEvent,event_id)
        if not e or e.kind!='card.help':raise HTTPException(404)
        get_run(s,u,e.run_id);e.data={**e.data,'acknowledged_by':u.id,'acknowledged_at':now().isoformat()};log(s,u,'card.help.ack',{'event_id':e.id,'run_id':e.run_id});s.commit();return {'status':'ok'}

    @app.post('/api/issues')
    async def issue(description:str=Form(min_length=1,max_length=5000),run_id:int|None=Form(default=None),attachment:UploadFile|None=File(default=None),u=Depends(current),s=Depends(db)):
        if run_id is not None:get_run(s,u,run_id)
        filename=None
        if attachment:
            if attachment.content_type not in ('image/png','image/jpeg','image/webp'):raise HTTPException(422,'Приложите изображение PNG, JPEG или WebP')
            data=await attachment.read(5*1024*1024+1)
            if len(data)>5*1024*1024:raise HTTPException(413,'Изображение больше 5 МБ')
            suffix={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp'}[attachment.content_type];filename=secrets.token_hex(24)+suffix
            root=Path('data/runtime/issues');root.mkdir(parents=True,exist_ok=True);(root/filename).write_bytes(data)
        row=TrainingIssue(user_id=u.id,run_id=run_id,description=description,attachment=filename);s.add(row);s.flush();log(s,u,'issue.create',{'issue_id':row.id,'run_id':run_id});s.commit();return {'id':row.id}

    @app.get('/api/issues')
    def issues(u=Depends(current),s=Depends(db)):
        if u.role!='admin':raise HTTPException(403)
        return [{'id':x.id,'description':x.description,'user_id':x.user_id,'run_id':x.run_id,'at':x.created_at,'attachment_url':f'/api/issues/{x.id}/attachment' if x.attachment else None} for x in s.scalars(select(TrainingIssue).order_by(TrainingIssue.id.desc()).limit(200))]

    @app.get('/api/issues/{issue_id}/attachment')
    def issue_attachment(issue_id:int,u=Depends(current),s=Depends(db)):
        row=s.get(TrainingIssue,issue_id)
        if not row or (u.role!='admin' and row.user_id!=u.id) or not row.attachment:raise HTTPException(404)
        return FileResponse(Path('data/runtime/issues')/row.attachment)
