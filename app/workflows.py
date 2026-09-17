"""Classifier, saved cards and teacher-led lessons; all changes are audited."""
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select, func, or_, and_, cast, String
from sqlalchemy.orm import Session
from app.models import (User, Scenario, SessionRun, Audit, ClassifierVersion, IncidentType,
                        ScenarioSettings, Material, Lesson, RunContext, CardEvent, ExpertReview, AccountState, AiJob, SipAccount, VoipCall, CardAttachment, TrainingMessage)
from app.schemas import (CardIn, DraftIn, ResolveIn, SettingsIn, LessonIn, StatusIn,
                         WorkCallIn, ReviewIn, UserIn, UserUpdateIn)
from app.classifier import active_workbook, parse_workbook, resolve_rules, FLAGS, SERVICE_NAMES
from app.training import available_statuses, card_indicator, REJECTED, REFUSED, TERMINAL

SOURCES=Path('source_materials')

def now(): return datetime.now(timezone.utc)
def aware(value): return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value

def log(s,u,action,details):
    s.add(Audit(user_id=u.id,action=action,details=details))

def import_classifier(s):
    path=active_workbook(SOURCES)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    version=s.scalar(select(ClassifierVersion).where(ClassifierVersion.sha256==digest))
    if version: return version
    parsed=parse_workbook(path)
    version=ClassifierVersion(sha256=digest,filename=parsed['filename'],sheet=parsed['sheet'],
                              manifest={'groups':parsed['groups'],'columns':parsed['columns'],'count':len(parsed['items'])})
    s.add(version);s.flush()
    for item in parsed['items']:
        s.add(IncidentType(version_id=version.id,code=item['code'],category=item['category'],
                           title=item['title'],source_row=item['source_row'],data=item))
    s.add(Audit(user_id=None,action='classifier.import',details={'version_id':version.id,'count':len(parsed['items']),'sha256':digest}))
    s.commit();return version

def import_materials(s):
    path=Path('data/imports/tickets.json')
    if not path.exists(): return
    payload=json.loads(path.read_text())
    changed=0;keys=set()
    for item in payload['tasks']:
        key=f"{payload['source_sha256']}:{item['page']}:{item['number']}"
        keys.add(key)
        existing=s.scalar(select(Material).where(Material.source_key==key))
        if existing:
            if existing.content!=item: existing.content=item;changed+=1
            continue
        s.add(Material(source_key=key,title=f"Билет {item['ticket']} · задача {item['number']}",
                       source={'filename':payload['source_file'],'sha256':payload['source_sha256'],
                               'page':item['page'],'ticket':item['ticket'],'number':item['number'],'method':'OCR, требует проверки'},
                       content=item));changed+=1
    for existing in s.scalars(select(Material).where(Material.source_key.startswith(payload['source_sha256']))):
        if existing.source_key not in keys and not existing.content.get('superseded'):
            existing.content={**existing.content,'superseded':True};changed+=1
    if changed: s.add(Audit(user_id=None,action='materials.import',details={'tasks':len(payload['tasks']),'changed':changed,'sha256':payload['source_sha256']}))
    s.commit()

def settings_for(s,scenario):
    return s.get(ScenarioSettings,scenario.id)

def check_scenario_access(s,u,scenario,*,explicit_sms=False):
    settings=settings_for(s,scenario)
    if u.role=='student':
        if settings and not settings.published: raise HTTPException(403,'Сценарий не утверждён преподавателем')
        assigned=list(s.scalars(select(Lesson)))
        # When teachers assign lessons to a student, only their assigned scenarios
        # are available. Standalone demo practice remains possible before assignment.
        own=[lesson for lesson in assigned if u.id in lesson.student_ids]
        if explicit_sms and settings and settings.mode=='dispatch':
            raise HTTPException(422,'Для SMS выберите сценарий карточки 112, а не ДДС')
        if settings and settings.mode=='dispatch' and not any(lesson.status=='active' and scenario.id in lesson.scenario_ids for lesson in own):
            raise HTTPException(403,'Карточка ДДС доступна в назначенном активном занятии')
        # A teacher-directed SMS is an explicit assignment independent of lessons.
        # Persisted messages also authorize acceptance and resuming a skipped card.
        sms_assigned=explicit_sms or s.scalar(select(TrainingMessage.id).where(
            TrainingMessage.student_id==u.id,TrainingMessage.scenario_id==scenario.id
        ).limit(1)) is not None
        if own and not sms_assigned and not any(lesson.status=='active' and scenario.id in lesson.scenario_ids for lesson in own):
            raise HTTPException(403,'Сценарий не назначен вам на активном занятии')

def assert_teacher(u):
    if u.role!='teacher': raise HTTPException(403,'Функция доступна преподавателю')

def assert_editable(s,u,scenario):
    if u.role not in ('teacher','admin'): raise HTTPException(403)
    if u.role=='teacher' and scenario.created_by!=u.id: raise HTTPException(403,'Нельзя изменять сценарий другого преподавателя')
    if s.scalar(select(SessionRun.id).where(SessionRun.scenario_id==scenario.id,SessionRun.finished_at.is_(None)).limit(1)):
        raise HTTPException(409,'Сценарий используется в активной тренировке')
    for lesson in s.scalars(select(Lesson).where(Lesson.status=='active')):
        if scenario.id in lesson.scenario_ids: raise HTTPException(409,'Сценарий используется в активном занятии')

def resolved_card(s,card):
    from app.questionnaires import answer_flags
    data=card.model_dump()
    questionnaire_flags=answer_flags(card.questionnaire_answers)
    identifiers=list(dict.fromkeys(card.classifier_ids+([card.classifier_id] if card.classifier_id else [])))
    flags={**card.flags,'victims':card.victims or card.flags.get('victims',False),
           'access_blocked':card.access_blocked or card.flags.get('access_blocked',False),
           'life_danger':card.life_danger or card.flags.get('life_danger',False)}
    flags.update(questionnaire_flags)
    if card.victims_count is not None:
        flags['victims']=card.victims_count>0
    data['victims']=flags.get('victims',False)
    if card.excluded_services and not card.service_override_reason.strip():raise HTTPException(422,'Укажите причину исключения автоматически назначенных служб')
    unknown=set(flags)-set(FLAGS)
    if unknown: raise HTTPException(422,'Неизвестные признаки ЕКП: '+', '.join(sorted(unknown)))
    types=[];dispatch={}
    for identifier in identifiers:
        incident=s.get(IncidentType,identifier)
        if not incident: raise HTTPException(422,'Неизвестный тип ЕКП')
        types.append({'id':incident.id,'code':incident.code,'title':incident.title,'features':incident.data['features'],
                      'version_id':incident.version_id,'source_row':incident.source_row,'extra_questions':incident.data['extra_questions']})
        for service in resolve_rules(incident.data['rules'],flags):
            if service['code'] in dispatch: dispatch[service['code']]['mappings']+=service['mappings']
            else: dispatch[service['code']]=service
    for code in card.excluded_services:
        if code in dispatch:dispatch.pop(code)
    for code in card.services:
        if code in card.excluded_services:continue
        if code not in dispatch:
            dispatch[code]={'code':code,'name':SERVICE_NAMES.get(code,code),'visible':True,'origin':'Вручную','mappings':[]}
    data['classifier_ids']=identifiers;data['classifications']=types;data['flags']=flags
    if types: data['incident_type']='; '.join(item['title'] for item in types)
    if not data['address'].strip():
        data['address']=' '.join(str(data[key]) for key in ('city','street','house','building','structure','apartment') if data[key])
    for service in dispatch.values():
        main_codes={'MCHS':'101','Police':'102','AMBULANCE':'103','MOSGAZ':'104','MOSLIFT':'EKP54','AUTOROADS':'EKP37','MOSVODOCANAL':'EKP50','METRO':'EKP49','OEK':'EKP53','MOSGORTRANS':'EKP38','MOESK':'EKP52','MOEK':'EKP51','MZD':'EKP61','MGTS':'EKP47','MOSVODOSTOK':'EKP66','MOSCOLLECTOR':'EKP60','GORMOST':'GORMOST','GKH':'GORHOZ'}
        service['main']=any(service['code'] in [main_codes.get(v.strip(),v.strip()) for v in t.data.get('main_service','').split(',')] for t in [s.get(IncidentType,i) for i in identifiers])
    data['services']=list(dispatch);data['dispatch']=list(dispatch.values())
    return data

def get_run(s,u,run_id,write=False):
    query=select(SessionRun).where(SessionRun.id==run_id)
    run=s.scalar(query.with_for_update() if write else query)
    if not run: raise HTTPException(404)
    context=s.get(RunContext,run.id)
    if u.role=='student' and run.student_id!=u.id: raise HTTPException(404)
    if u.role=='teacher':
        lesson=s.get(Lesson,context.lesson_id) if context and context.lesson_id else None
        scenario=s.get(Scenario,run.scenario_id)
        if (lesson and lesson.teacher_id!=u.id) or (not lesson and scenario.created_by!=u.id): raise HTTPException(403,'Тренировка другого преподавателя')
    return run,context

def ensure_writable(run,context,s):
    if run.finished_at: raise HTTPException(409,'Тренировка завершена')
    if context and context.lesson_id:
        lesson=s.get(Lesson,context.lesson_id)
        if lesson.status!='active': raise HTTPException(409,'Преподаватель завершил занятие')

def events_for(s,run):
    return list(s.scalars(select(CardEvent).where(CardEvent.run_id==run.id).order_by(CardEvent.id)))

def history_for(events):
    result={}
    for event in events:
        if event.kind=='service.status':
            result.setdefault(event.data['service_code'],[]).append({**event.data,'at':aware(event.at).isoformat(),'user_id':event.user_id})
    return result

def elapsed_seconds(run,context,at=None):
    snapshot=context.scenario_snapshot if context else {}
    if run.finished_at and (run.report or {}).get('skipped'):
        return run.report['elapsed_seconds']
    if snapshot.get('mode','call')=='call' and context and context.registered_at and 'registration_elapsed_seconds' in snapshot:
        return snapshot['registration_elapsed_seconds']
    active_since=snapshot.get('timer_active_since')
    anchor=datetime.fromisoformat(active_since) if active_since else aware(run.started_at)
    cutoff=aware(context.registered_at) if context and context.registered_at and snapshot.get('mode','call')=='call' else (at or aware(run.finished_at) or now())
    return max(0,int(snapshot.get('timer_elapsed_seconds',0)+max(0,(cutoff-anchor).total_seconds())))

def postprocessing_seconds(run,context,events,at=None):
    if not context or not context.registered_at:return 0
    start=aware(context.registered_at);end=aware(run.finished_at) or at or now()
    paused=None;pause_seconds=0
    for e in sorted(events,key=lambda e:(aware(e.at),e.id or 0)):
        stamp=aware(e.at)
        if stamp>end:break
        if e.kind=='card.skip':paused=max(start,stamp)
        elif e.kind=='card.resume' and paused is not None:
            pause_seconds+=max(0,(stamp-paused).total_seconds());paused=None
    if paused is not None:pause_seconds+=max(0,(end-paused).total_seconds())
    return max(0,int((end-start).total_seconds()-pause_seconds))

def run_payload(s,run,context):
    events=events_for(s,run);history=history_for(events)
    if context: context.registered_at=aware(context.registered_at)
    snapshot=context.scenario_snapshot if context else {}
    indicator=card_indicator(context,{code:records for code,records in history.items() if not run.answers.get('dispatch') or any(service['code']==code and service.get('visible',True) for service in run.answers['dispatch'])},now())
    previous_indicator=snapshot.get('last_indicator')
    if context and context.registered_at and previous_indicator!=indicator:
        if indicator in ('Не оповещено','Не завершено') or previous_indicator in ('Не оповещено','Не завершено'):
            s.add(CardEvent(run_id=run.id,user_id=run.student_id,kind='card.indicator',data={'before':previous_indicator,'after':indicator,'automatic':True}))
            s.add(Audit(user_id=run.student_id,action='card.indicator',details={'run_id':run.id,'before':previous_indicator,'after':indicator,'automatic':True}))
        context.scenario_snapshot={**snapshot,'last_indicator':indicator};s.commit()
    from app.models import TrainingMessage
    unread=s.scalar(select(func.count()).select_from(TrainingMessage).where(TrainingMessage.run_id==run.id,TrainingMessage.read.is_(False)))
    return {'run_id':run.id,'scenario_id':run.scenario_id,'student_id':run.student_id,
            'started_at':aware(run.started_at),'finished_at':aware(run.finished_at),
            'timer_started_at':now()-timedelta(seconds=elapsed_seconds(run,context)), 'elapsed_seconds':elapsed_seconds(run,context),
            'card':run.answers,'revision':context.revision if context else 0,'checked':bool(context and context.checked),
            'operator_id':snapshot.get('origin_operator_id',str(run.student_id)),'workstation':snapshot.get('origin_workstation',str(run.student_id)),
            'mode':snapshot.get('mode','call'),'service_code':snapshot.get('service_code','112'),'lesson_id':context.lesson_id if context else None,
            'norm_seconds':snapshot.get('expected',{}).get('norm_seconds',30),
            'timer_frozen':bool(context and context.registered_at and snapshot.get('mode','call')=='call'),
            'postprocessing_seconds':postprocessing_seconds(run,context,events),
            'last_request_id':snapshot.get('last_draft_request_id'),'registered_at':context.registered_at if context else None,
            'status':'Пропущена' if (run.report or {}).get('skipped') else indicator,'service_history':history,
            'available_statuses':{code:available_statuses(records[-1]['status'],code) for code,records in history.items()},
            'events':[{'id':e.id,'at':aware(e.at),'kind':e.kind,'data':e.data,'user_id':e.user_id} for e in events],
            'parent_run_id':snapshot.get('parent_run_id'),'sms_unread':unread,'reminder':snapshot.get('reminder'),'report':run.report}

def register_card(s,u,run,context):
    if context.registered_at: return
    registration_elapsed=elapsed_seconds(run,context)
    context.registered_at=now()
    context.scenario_snapshot={**context.scenario_snapshot,'registration_elapsed_seconds':registration_elapsed}
    s.add(CardEvent(run_id=run.id,user_id=u.id,kind='card.register',data={'elapsed_seconds':registration_elapsed}))
    for code in run.answers.get('services',[]):
        s.add(CardEvent(run_id=run.id,user_id=u.id,kind='service.status',data={'service_code':code,'status':'Добавлена','comment':'','unit_number':''}))
    log(s,u,'card.register',{'run_id':run.id})

def lesson_progress(s,lesson,student_id):
    attempts=list(s.execute(select(SessionRun,RunContext).join(RunContext).where(
        RunContext.lesson_id==lesson.id,SessionRun.student_id==student_id
    ).order_by(SessionRun.id)))
    latest={}
    def priority(run):return 2 if not run.finished_at else (0 if (run.report or {}).get('skipped') else 1)
    for run,context in attempts:
        previous=latest.get(run.scenario_id)
        if not previous or priority(run)>=priority(previous[0]):latest[run.scenario_id]=(run,context)
    scenarios={item.id:item for item in s.scalars(select(Scenario).where(Scenario.id.in_(lesson.scenario_ids)))}
    tasks=[]
    for identifier in dict.fromkeys(lesson.scenario_ids):
        previous=latest.get(identifier);run,context=previous if previous else (None,None)
        status='pending' if not run else ('in_progress' if not run.finished_at else ('skipped' if (run.report or {}).get('skipped') else 'completed'))
        title=scenarios[identifier].title if identifier in scenarios else 'Задание №'+str(identifier)
        tasks.append({'scenario_id':identifier,'title':context.scenario_snapshot.get('title',title) if context else title,
                      'status':status,'run_id':run.id if run else None,'score':run.score if run and status=='completed' else None,
                      'started_at':aware(run.started_at) if run else None,'finished_at':aware(run.finished_at) if run else None,
                      'elapsed_seconds':(run.report or {}).get('elapsed_seconds') if run and run.finished_at else None})
    counts={status:sum(task['status']==status for task in tasks) for status in ('pending','in_progress','skipped','completed')}
    return {'tasks':tasks,'counts':{**counts,'total':len(tasks)},'all_completed':bool(tasks) and counts['completed']==len(tasks)}

def lesson_active_run(s,u,lesson):
    s.scalar(select(User).where(User.id==u.id).with_for_update())
    active=s.scalar(select(SessionRun).where(SessionRun.student_id==u.id,SessionRun.finished_at.is_(None)).order_by(SessionRun.id.desc()).with_for_update())
    if active:
        context=s.get(RunContext,active.id)
        if not context or context.lesson_id!=lesson.id:
            raise HTTPException(409,'У вас открыта карточка другого занятия. Завершите её или нажмите «Пропустить карточку».')
        return active,context
    return None

def begin_run(s,u,scenario,lesson=None):
    check_scenario_access(s,u,scenario)
    previous=s.scalar(select(SessionRun).join(RunContext).where(
        SessionRun.student_id==u.id,SessionRun.scenario_id==scenario.id,
        RunContext.lesson_id==lesson.id if lesson else RunContext.lesson_id.is_(None)
    ).order_by(SessionRun.id.desc()).with_for_update())
    if lesson:
        completed=s.scalars(select(SessionRun).join(RunContext).where(SessionRun.student_id==u.id,SessionRun.scenario_id==scenario.id,RunContext.lesson_id==lesson.id,SessionRun.finished_at.is_not(None)))
        if any(not (run.report or {}).get('skipped') for run in completed):
            raise HTTPException(409,'Задание этого занятия уже выполнено. Откройте результат; повторный запуск заблокирован.')
    if previous and previous.finished_at and (previous.report or {}).get('skipped'):
        context=s.get(RunContext,previous.id)
        resumed_at=now();saved_elapsed=previous.report['elapsed_seconds']
        context.scenario_snapshot={**context.scenario_snapshot,'timer_elapsed_seconds':saved_elapsed,'timer_active_since':resumed_at.isoformat()}
        previous.finished_at=None;previous.report=None;previous.score=None
        s.add(CardEvent(run_id=previous.id,user_id=u.id,kind='card.resume',at=resumed_at,data={'elapsed_seconds':saved_elapsed}))
        log(s,u,'run.resume',{'run_id':previous.id,'elapsed_seconds':saved_elapsed})
        s.commit();return previous,context
    settings=settings_for(s,scenario)
    snapshot={'title':scenario.title,'category':scenario.category,'caller_text':scenario.caller_text,
              'expected':scenario.expected,'mode':lesson.mode if lesson else (settings.mode if settings else 'call'),
              'service_code':lesson.service_code if lesson else '112'}
    run=SessionRun(scenario_id=scenario.id,student_id=u.id)
    s.add(run);s.flush()
    context=RunContext(run_id=run.id,lesson_id=lesson.id if lesson else None,scenario_snapshot=snapshot)
    snapshot['origin_operator_id']='Учебный 112' if snapshot['mode']=='dispatch' else str(u.id)
    snapshot['origin_workstation']='Учебный 112' if snapshot['mode']=='dispatch' else str(u.id)
    context.scenario_snapshot=snapshot
    s.add(context)
    if snapshot['mode']=='dispatch':
        initial=settings.initial_card if settings else {}
        if not initial.get('address') or not initial.get('description'): raise HTTPException(422,'Для задания ДДС нужна подготовленная карточка')
        run.answers=resolved_card(s,CardIn.model_validate(initial))
        if snapshot['service_code'] not in run.answers['services']: raise HTTPException(422,'Учебная служба отсутствует в списке оповещения карточки')
        register_card(s,u,run,context)
        s.add(CardEvent(run_id=run.id,user_id=u.id,kind='service.status',data={'service_code':snapshot['service_code'],'status':'Получена службой','comment':'','unit_number':''}))
    else:
        run.answers=CardIn(aon=scenario.expected.get('aon','')).model_dump()
    log(s,u,'run.start',{'run_id':run.id,'lesson_id':lesson.id if lesson else None})
    s.commit();return run,context

def finalize_run(s,u,run,context,card,evaluate,commit=True):
    snapshot=context.scenario_snapshot if context else None
    scenario=Scenario(**{key:snapshot[key] for key in ('title','category','caller_text','expected')}) if snapshot else s.get(Scenario,run.scenario_id)
    elapsed=elapsed_seconds(run,context)
    if context and snapshot.get('mode')=='dispatch':
        history=history_for(events_for(s,run));own=history.get(snapshot['service_code'],[])
        current_status=own[-1]['status'] if own else 'Добавлена'
        first=next((e for e in own if e['status'] not in ('Добавлена','Получена службой')),None)
        reaction=max(0,int((datetime.fromisoformat(first['at'])-aware(context.registered_at)).total_seconds())) if first else elapsed
        expected_status=scenario.expected.get('service_status','Принята')
        norm=int(scenario.expected.get('norm_seconds',30))
        text=' '.join(e.get('comment','') for e in own)
        import re
        def words(value): return set(re.findall(r'[а-яёa-z0-9]+',value.lower()))
        expected_words=words(scenario.expected.get('operator_comment',''))
        overlap=len(expected_words&words(text))/max(1,len(expected_words))
        parts={'Статус реагирования':40 if current_status==expected_status else 0,
               'Комментарий диспетчера':30 if overlap>=.65 else 15 if overlap>=.3 else 0,
               'Время реакции':30 if first and reaction<=norm else 0}
        report={'score':sum(parts.values()),'parts':parts,'parts_max':{'Статус реагирования':40,'Комментарий диспетчера':30,'Время реакции':30},
                'elapsed_seconds':elapsed,'reaction_seconds':reaction,'norm_seconds':norm,
                'time_deviation_seconds':reaction-norm,'errors':[f'{name}: критерий не выполнен полностью' for name,value in parts.items() if value<{'Статус реагирования':40,'Комментарий диспетчера':30,'Время реакции':30}[name]],
                'note':'Учебная оценка ДДС: статус, ключевые сведения комментария и время первичного подтверждения. Требуется экспертный разбор.'}
    else:
        data=resolved_card(s,card);run.answers=data
        report=evaluate(scenario,CardIn.model_validate(data),elapsed)
        report['time_deviation_seconds']=elapsed-report['norm_seconds']
        if context and data.get('description') and (data.get('address') or not any(x.get('visible',True) for x in data.get('dispatch',[]))): register_card(s,u,run,context)
    if context and snapshot.get('mode')=='dispatch':
        from app.ml import apply_neural
        apply_neural(report,scenario.expected.get('operator_comment',''),text,'Комментарий диспетчера',30)
    if context and snapshot.get('mode','call')=='call':
        report['postprocessing_seconds']=postprocessing_seconds(run,context,events_for(s,run))
    from app.models import OperatorPresence
    operator=s.get(OperatorPresence,run.student_id)
    if operator:operator.available_after=now()+timedelta(seconds=10)
    report['run_id']=run.id
    from app.grammar import check_grammar
    report['grammar']=check_grammar(text if context and snapshot.get('mode')=='dispatch' else run.answers.get('description','')+'\n'+run.answers.get('operator_comment',''))
    run.finished_at=now();run.score=report['score'];run.report=report
    s.add(CardEvent(run_id=run.id,user_id=u.id,kind='card.finish',data={'score':run.score}))
    log(s,u,'run.finish',{'run_id':run.id,'score':run.score})
    if commit: s.commit()
    return report

def register_routes(app,db,current,evaluate,pwd):
    @app.get('/api/users')
    def users(u=Depends(current),s=Depends(db)):
        if u.role!='admin': raise HTTPException(403)
        blocked={x.user_id:x.blocked for x in s.scalars(select(AccountState))}
        return [{'id':x.id,'username':x.username,'role':x.role,'blocked':blocked.get(x.id,False)} for x in s.scalars(select(User).order_by(User.id))]

    @app.post('/api/users')
    def create_user(x:UserIn,u=Depends(current),s=Depends(db)):
        if u.role!='admin': raise HTTPException(403)
        if len(x.password.encode())>72: raise HTTPException(422,'Пароль должен занимать не более 72 байт')
        if s.scalar(select(User.id).where(User.username==x.username)): raise HTTPException(409,'Логин уже существует')
        user=User(username=x.username,password_hash=pwd.hash(x.password),role=x.role);s.add(user);s.flush()
        log(s,u,'user.create',{'user_id':user.id,'username':user.username,'role':user.role});s.commit();return {'id':user.id}

    @app.put('/api/users/{user_id}')
    def update_user(user_id:int,x:UserUpdateIn,u=Depends(current),s=Depends(db)):
        if u.role!='admin': raise HTTPException(403)
        user=s.scalar(select(User).where(User.id==user_id).with_for_update())
        if not user: raise HTTPException(404)
        if user.id==u.id and (x.blocked or x.role!='admin'): raise HTTPException(409,'Нельзя заблокировать себя или снять собственную роль администратора')
        if s.scalar(select(SessionRun.id).where(SessionRun.student_id==user_id,SessionRun.finished_at.is_(None)).limit(1)):
            raise HTTPException(409,'Пользователь проходит активную тренировку')
        if s.scalar(select(Lesson.id).where(Lesson.teacher_id==user_id,Lesson.status=='active').limit(1)):
            raise HTTPException(409,'Преподаватель ведёт активное занятие')
        account=s.get(AccountState,user_id)
        if not account: account=AccountState(user_id=user_id);s.add(account)
        user.role=x.role;account.blocked=x.blocked
        log(s,u,'user.update',{'user_id':user_id,'role':x.role,'blocked':x.blocked});s.commit();return {'status':'ok'}

    @app.delete('/api/users/{user_id}')
    def delete_user(user_id:int,u=Depends(current),s:Session=Depends(db)):
        if u.role!='admin': raise HTTPException(403)
        if user_id==u.id: raise HTTPException(409,'Нельзя удалить собственную учётную запись')
        user=s.get(User,user_id)
        if not user: raise HTTPException(404)
        active=s.scalar(select(SessionRun.id).where(SessionRun.student_id==user_id,SessionRun.finished_at.is_(None)).limit(1))
        if active: raise HTTPException(409,'Сначала завершите активную тренировку пользователя')
        from app.main import delete_run_data
        for run_id in list(s.scalars(select(SessionRun.id).where(SessionRun.student_id==user_id))):delete_run_data(s,run_id)
        for scenario in s.scalars(select(Scenario).where(Scenario.created_by==user_id)):scenario.created_by=None
        s.query(Lesson).filter(Lesson.teacher_id==user_id).delete(synchronize_session=False)
        s.query(AiJob).filter(AiJob.teacher_id==user_id).delete(synchronize_session=False)
        s.query(ExpertReview).filter(ExpertReview.teacher_id==user_id).delete(synchronize_session=False)
        s.query(CardEvent).filter(CardEvent.user_id==user_id).delete(synchronize_session=False)
        from app.models import OperatorPresence,TrainingMessage,TrainingIssue
        s.query(OperatorPresence).filter(OperatorPresence.user_id==user_id).delete(synchronize_session=False)
        s.query(TrainingMessage).filter(or_(TrainingMessage.student_id==user_id,TrainingMessage.teacher_id==user_id)).delete(synchronize_session=False)
        s.query(TrainingIssue).filter(TrainingIssue.user_id==user_id).delete(synchronize_session=False)
        s.query(SipAccount).filter(SipAccount.user_id==user_id).delete(synchronize_session=False)
        s.query(AccountState).filter(AccountState.user_id==user_id).delete(synchronize_session=False)
        log(s,u,'user.delete',{'user_id':user_id,'username':user.username});s.delete(user);s.commit();return {'status':'deleted','user_id':user_id}

    @app.post('/api/runs/{run_id}/checked')
    def checked(run_id:int,u=Depends(current),s=Depends(db)):
        assert_teacher(u);run,context=get_run(s,u,run_id,True)
        if not context or not context.registered_at: raise HTTPException(409,'Карточка ещё не зарегистрирована')
        context.checked=True
        s.add(CardEvent(run_id=run.id,user_id=u.id,kind='card.checked',data={'checked':True}))
        log(s,u,'card.checked',{'run_id':run.id});s.commit();return run_payload(s,run,context)

    @app.get('/api/classifier')
    def classifier_info(u=Depends(current),s=Depends(db)):
        version=s.scalar(select(ClassifierVersion).order_by(ClassifierVersion.id.desc()))
        return {'version':{'id':version.id,'filename':version.filename,'sha256':version.sha256,'count':version.manifest['count'],'groups':version.manifest['groups']} if version else None,
                'flags':FLAGS,'territory_note':'ЕКП содержит агрегированные территориальные службы; точные адресные полигоны заказчика не предоставлены.'}

    @app.post('/api/classifier/import')
    def reimport(u=Depends(current),s=Depends(db)):
        if u.role!='admin': raise HTTPException(403)
        try: version=import_classifier(s)
        except ValueError as error: raise HTTPException(422,str(error))
        if not version: raise HTTPException(404,'Исходный XLSX не найден')
        log(s,u,'classifier.import.request',{'version_id':version.id});s.commit()
        return {'version_id':version.id,'count':version.manifest['count']}

    @app.get('/api/classifier/types')
    def types(q:str='',category:str='',limit:int=80,u=Depends(current),s=Depends(db)):
        version=s.scalar(select(ClassifierVersion).order_by(ClassifierVersion.id.desc()))
        if not version: return []
        query=select(IncidentType).where(IncidentType.version_id==version.id)
        if category: query=query.where(IncidentType.category==category)
        if q:
            import re
            aliases={'101':'пожар','104':'газ','103':'медицин','102':'правопоряд'}
            if q.strip() in aliases:
                query=query.where(IncidentType.category.ilike('%'+aliases[q.strip()]+'%'))
            else:
                for token in re.findall(r'[а-яёa-z0-9]+',q.lower()):
                    escaped=token.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
                    query=query.where(or_(IncidentType.title.ilike(f'%{escaped}%',escape='\\'),IncidentType.code.ilike(f'%{escaped}%',escape='\\')))
        return [{'id':x.id,'code':x.code,'title':x.title,'category':x.category,'features':x.data['features'],'extra_questions':x.data['extra_questions'],'source_row':x.source_row} for x in s.scalars(query.order_by(IncidentType.source_row).limit(max(1,min(limit,1500))))]

    @app.get('/api/classifier/types/{type_id}')
    def historical_type(type_id:int,u=Depends(current),s=Depends(db)):
        incident=s.get(IncidentType,type_id)
        if not incident: raise HTTPException(404)
        return {'id':incident.id,'code':incident.code,'title':incident.title,'category':incident.category,
                'features':incident.data['features'],'extra_questions':incident.data['extra_questions'],
                'source_row':incident.source_row,'version_id':incident.version_id}

    @app.post('/api/classifier/resolve')
    def resolve(x:ResolveIn,u=Depends(current),s=Depends(db)):
        data=resolved_card(s,CardIn(classifier_ids=x.classifier_ids,flags=x.flags))
        return {'classifications':data['classifications'],'services':data['dispatch']}

    @app.get('/api/materials')
    def materials(u=Depends(current),s=Depends(db)):
        if u.role=='student': raise HTTPException(403)
        return [{'id':m.id,'title':m.title,'source':m.source,'content':m.content} for m in s.scalars(select(Material).order_by(Material.id)) if not m.content.get('superseded')]

    @app.get('/api/materials/source/{kind}')
    def source(kind:str,u=Depends(current)):
        patterns={'manual':'Работа*.pdf','tickets':'Билеты*.pdf','spec':'9.*.pdf'}
        if kind not in patterns: raise HTTPException(404)
        if u.role=='student' and kind!='manual': raise HTTPException(403)
        path=next(SOURCES.glob(patterns[kind]),None)
        if not path: raise HTTPException(404)
        return FileResponse(path,media_type='application/pdf',filename=path.name,content_disposition_type='inline')

    @app.put('/api/scenarios/{scenario_id}/settings')
    def configure(scenario_id:int,x:SettingsIn,u=Depends(current),s=Depends(db)):
        scenario=s.get(Scenario,scenario_id)
        if not scenario: raise HTTPException(404)
        assert_editable(s,u,scenario)
        if x.generation_job_id:
            job=s.get(AiJob,x.generation_job_id)
            if not job or job.teacher_id!=u.id or job.status!='done':raise HTTPException(422,'Недоступный результат генерации')
        if x.published and not all(scenario.expected.get(key) for key in ('incident_type','operator_comment')): raise HTTPException(422,'Заполните и проверьте эталон')
        if x.mode=='dispatch' and x.published and (not x.initial_card.address or not x.initial_card.description): raise HTTPException(422,'Подготовьте карточку ДДС')
        data=resolved_card(s,x.initial_card)
        setting=s.get(ScenarioSettings,scenario_id)
        if not setting: setting=ScenarioSettings(scenario_id=scenario_id);s.add(setting)
        if x.generation_job_id:setting.source={**(setting.source or {}),'generation':job.result['provenance'],'generation_job_id':job.id}
        setting.published=x.published;setting.difficulty=x.difficulty;setting.mode=x.mode;setting.initial_card=data
        log(s,u,'scenario.configure',{'scenario_id':scenario_id,'published':x.published,'mode':x.mode});s.commit()
        return {'status':'ok'}

    @app.get('/api/teaching/students')
    def students(u=Depends(current),s=Depends(db)):
        assert_teacher(u)
        return [{'id':x.id,'username':x.username} for x in s.scalars(select(User).where(User.role=='student'))]

    def validate_lesson(x,u,s):
        x.title=x.title.strip()
        if not x.title:raise HTTPException(422,'Введите название занятия')
        x.scenario_ids=list(dict.fromkeys(x.scenario_ids));x.student_ids=list(dict.fromkeys(x.student_ids))
        if x.mode=='call':x.service_code='TERRITORY'
        for identifier in set(x.scenario_ids):
            scenario=s.get(Scenario,identifier)
            if not scenario or scenario.created_by!=u.id: raise HTTPException(403,'Выберите свои сценарии')
            setting=settings_for(s,scenario)
            if setting and (not setting.published or setting.mode!=x.mode): raise HTTPException(422,'Нужен утверждённый сценарий выбранного режима')
            if x.mode=='dispatch':
                if not setting or not setting.initial_card.get('address'): raise HTTPException(422,'Для ДДС требуется исходная карточка')
                if x.service_code not in setting.initial_card.get('services',[]): raise HTTPException(422,'Службы занятия нет в карточке ДДС')
        for identifier in set(x.student_ids):
            student=s.get(User,identifier)
            if not student or student.role!='student': raise HTTPException(422,'Неверный обучающийся')

    @app.post('/api/lessons')
    def create_lesson(x:LessonIn,u=Depends(current),s=Depends(db)):
        assert_teacher(u);validate_lesson(x,u,s)
        lesson=Lesson(**x.model_dump(),teacher_id=u.id);s.add(lesson);s.flush();log(s,u,'lesson.create',{'lesson_id':lesson.id});s.commit();return {'id':lesson.id}

    @app.put('/api/lessons/{lesson_id}')
    def update_lesson(lesson_id:int,x:LessonIn,u=Depends(current),s=Depends(db)):
        assert_teacher(u)
        lesson=s.scalar(select(Lesson).where(Lesson.id==lesson_id).with_for_update())
        if not lesson:raise HTTPException(404,'Занятие не найдено')
        if lesson.teacher_id!=u.id:raise HTTPException(403)
        if lesson.status!='prepared':raise HTTPException(409,'Можно редактировать только подготовленное занятие')
        validate_lesson(x,u,s)
        for key,value in x.model_dump().items():setattr(lesson,key,value)
        log(s,u,'lesson.update',{'lesson_id':lesson.id});s.commit();return {'id':lesson.id}

    @app.get('/api/lessons')
    def lessons(u=Depends(current),s=Depends(db)):
        query=select(Lesson).order_by(Lesson.id.desc())
        if u.role=='teacher': query=query.where(Lesson.teacher_id==u.id)
        rows=[]
        for lesson in s.scalars(query):
            if u.role=='student' and u.id not in lesson.student_ids: continue
            rows.append({'id':lesson.id,'title':lesson.title,'status':lesson.status,'mode':lesson.mode,
                         'scenario_ids':lesson.scenario_ids,'student_ids':lesson.student_ids if u.role!='student' else [u.id],
                         'service_code':lesson.service_code,**(lesson_progress(s,lesson,u.id) if u.role=='student' else {})})
        return rows

    @app.post('/api/lessons/{lesson_id}/tasks/{scenario_id}/start')
    def start_lesson_task(lesson_id:int,scenario_id:int,u=Depends(current),s=Depends(db)):
        lesson=s.scalar(select(Lesson).where(Lesson.id==lesson_id).with_for_update())
        if not lesson:raise HTTPException(404)
        if u.role!='student' or u.id not in lesson.student_ids:raise HTTPException(403)
        if lesson.status!='active':raise HTTPException(409,'Занятие не запущено')
        if scenario_id not in lesson.scenario_ids:raise HTTPException(403,'Задание не назначено на этом занятии')
        active=lesson_active_run(s,u,lesson)
        if active:
            if active[0].scenario_id!=scenario_id:raise HTTPException(409,'Завершите текущую карточку или нажмите «Пропустить карточку».')
            return run_payload(s,*active)
        task=next(item for item in lesson_progress(s,lesson,u.id)['tasks'] if item['scenario_id']==scenario_id)
        if task['status']=='completed':raise HTTPException(409,'Задание уже выполнено. Откройте его результат.')
        run,context=begin_run(s,u,s.get(Scenario,scenario_id),lesson)
        return run_payload(s,run,context)

    @app.post('/api/lessons/{lesson_id}/{action}')
    def control_lesson(lesson_id:int,action:str,u=Depends(current),s=Depends(db)):
        lesson=s.scalar(select(Lesson).where(Lesson.id==lesson_id).with_for_update())
        if not lesson: raise HTTPException(404)
        if action=='next':
            if u.role!='student' or u.id not in lesson.student_ids: raise HTTPException(403)
            if lesson.status!='active': raise HTTPException(409,'Занятие не запущено')
            active=lesson_active_run(s,u,lesson)
            if active:return run_payload(s,*active)
            progress=lesson_progress(s,lesson,u.id)
            pending=[task['scenario_id'] for task in progress['tasks'] if task['status']=='pending']
            skipped=[task['scenario_id'] for task in progress['tasks'] if task['status']=='skipped']
            remaining=pending or skipped
            if not remaining:return {'done':True,**progress}
            import secrets
            scenario=s.get(Scenario,secrets.choice(remaining))
            run,context=begin_run(s,u,scenario,lesson);return run_payload(s,run,context)
        assert_teacher(u)
        if lesson.teacher_id!=u.id: raise HTTPException(403)
        if action=='start' and lesson.status=='prepared': lesson.status='active'
        elif action=='stop' and lesson.status=='active':
            lesson.status='finished'
            runs=list(s.scalars(select(SessionRun).join(RunContext).where(RunContext.lesson_id==lesson.id,SessionRun.finished_at.is_(None))))
            for run in runs:
                context=s.get(RunContext,run.id)
                finalize_run(s,u,run,context,CardIn.model_validate(run.answers or {}),evaluate,commit=False)
            lesson.status='finished'
        else: raise HTTPException(409,'Недопустимый переход состояния занятия')
        log(s,u,'lesson.'+action,{'lesson_id':lesson.id});s.commit();return {'status':lesson.status}

    @app.get('/api/cards')
    def cards(q:str=Query(default='',max_length=1000), student_id:int|None=Query(default=None), address:str=Query(default='',max_length=1000),
              descriptive_address:str=Query(default='',max_length=1000),borough:str=Query(default='',max_length=200),
              district:str=Query(default='',max_length=200),source:str=Query(default='',max_length=100),
              status:str=Query(default='',max_length=500),services:str=Query(default='',max_length=1000),channels:str=Query(default='',max_length=1000),
              caller:str=Query(default='',max_length=200),description:str=Query(default='',max_length=1000),incident:str=Query(default='',max_length=1000),
              operator:str=Query(default='',max_length=100),workstation:str=Query(default='',max_length=100),card_number:int|None=Query(default=None,ge=1),
              date_from:datetime|None=None,date_to:datetime|None=None,unread_sms:bool=False,limit:int=Query(default=200,ge=1,le=200),offset:int=Query(default=0,ge=0),
              u=Depends(current),s:Session=Depends(db)):
        query=select(SessionRun).order_by(SessionRun.id.desc())
        if u.role=='student':query=query.where(SessionRun.student_id==u.id)
        if u.role=='teacher':
            query=query.outerjoin(RunContext,RunContext.run_id==SessionRun.id).outerjoin(Lesson,Lesson.id==RunContext.lesson_id).join(Scenario,Scenario.id==SessionRun.scenario_id).where(or_(Lesson.teacher_id==u.id,and_(RunContext.lesson_id.is_(None),Scenario.created_by==u.id)))
            if student_id is not None: query=query.where(SessionRun.student_id==student_id)
        def pattern(value):return '%'+value.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        if q:query=query.where(or_(cast(SessionRun.id,String).ilike(pattern(q),escape='\\'),*[SessionRun.answers[key].as_string().ilike(pattern(q),escape='\\') for key in ['incident_type','address','description']]))
        for key,value in [('address',address),('descriptive_address',descriptive_address),('borough',borough),('district',district)]:
            if value:query=query.where(SessionRun.answers[key].as_string().ilike(pattern(value),escape='\\'))
        if source:query=query.where(SessionRun.answers['source_system'].as_string()==source)
        if date_from:query=query.where(SessionRun.started_at>=aware(date_from))
        if date_to:query=query.where(SessionRun.started_at<=aware(date_to))
        if card_number is not None:query=query.where(SessionRun.id==card_number)
        if caller:query=query.where(or_(*[SessionRun.answers[key].as_string().ilike(pattern(caller),escape='\\') for key in ['caller_name','aon','caller_phone','on_site_phone']]))
        for key,value in [('description',description),('incident_type',incident)]:
            if value:query=query.where(SessionRun.answers[key].as_string().ilike(pattern(value),escape='\\'))
        filtered=bool(status or services or channels or operator or workstation or unread_sms)
        result=[];matched=0
        for run in s.scalars(query if filtered else query.offset(offset).limit(limit)):
            payload=run_payload(s,run,s.get(RunContext,run.id))
            if filtered:
                if status and payload['status'] not in status.split(','):continue
                if services and not set(services.split(','))&set(payload['card'].get('services',[])):continue
                if channels and payload['card'].get('channel') not in channels.split(','):continue
                if operator and payload['operator_id']!=operator:continue
                if workstation and payload['workstation']!=workstation:continue
                if unread_sms and not payload['sms_unread']:continue
                matched+=1
                if matched<=offset:continue
            result.append(payload)
            if len(result)>=limit:break
        return result

    @app.get('/api/runs/{run_id}')
    def read_card(run_id:int,u=Depends(current),s=Depends(db)):
        run,context=get_run(s,u,run_id);return run_payload(s,run,context)

    @app.get('/api/active-run')
    def active_run(u=Depends(current),s=Depends(db)):
        if u.role!='student': return None
        run=s.scalar(select(SessionRun).where(SessionRun.student_id==u.id,SessionRun.finished_at.is_(None)).order_by(SessionRun.id.desc()))
        return run_payload(s,run,s.get(RunContext,run.id)) if run else None

    @app.post('/api/runs/{run_id}/skip')
    def skip_card(run_id:int,u=Depends(current),s=Depends(db)):
        if u.role!='student': raise HTTPException(403)
        run,context=get_run(s,u,run_id,True)
        if run.finished_at and (run.report or {}).get('skipped'): return run.report
        ensure_writable(run,context,s)
        skipped_at=now();saved_elapsed=elapsed_seconds(run,context,skipped_at)
        run.finished_at=skipped_at;run.score=None
        run.report={'run_id':run.id,'skipped':True,'skipped_at':run.finished_at.isoformat(),'skipped_by':u.id,'skipped_by_name':u.username,'elapsed_seconds':saved_elapsed,
                    'note':'Карточка пропущена без оценки. Сохранённые сведения доступны в истории.'}
        channels=[]
        for call in s.scalars(select(VoipCall).where(VoipCall.run_id==run.id,VoipCall.state.in_(['queued','ringing','answered']))):
            call.state='cancelled';call.ended_at=run.finished_at
            if call.channel: channels.append(call.channel)
        s.add(CardEvent(run_id=run.id,user_id=u.id,kind='card.skip',at=run.finished_at,data={'skipped_at':run.report['skipped_at'],'elapsed_seconds':run.report['elapsed_seconds'],'student_name':u.username}))
        log(s,u,'run.skip',{'run_id':run.id});s.commit()
        from app.telephony import ami_action
        for channel in channels:
            try: ami_action('Hangup',Channel=channel)
            except (OSError,ConnectionError): pass
        return run.report

    @app.put('/api/runs/{run_id}/draft')
    def save_draft(run_id:int,x:DraftIn,u=Depends(current),s=Depends(db)):
        if u.role!='student': raise HTTPException(403)
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        if not context: raise HTTPException(409,'Старая сессия: завершите её перед созданием новой')
        if context.scenario_snapshot.get('mode')=='dispatch': raise HTTPException(403,'Карточка поступившего происшествия доступна только для просмотра')
        if x.request_id and x.request_id==context.scenario_snapshot.get('last_draft_request_id'):
            return {'revision':context.revision,'card':run.answers}
        if x.revision!=context.revision: raise HTTPException(409,'Карточка изменена в другой вкладке. Обновите данные.')
        if context.registered_at: raise HTTPException(409,'Карточка уже зарегистрирована')
        run.answers=resolved_card(s,x.card);context.revision+=1
        if x.request_id: context.scenario_snapshot={**context.scenario_snapshot,'last_draft_request_id':x.request_id}
        log(s,u,'card.draft',{'run_id':run.id,'revision':context.revision});s.commit()
        return {'revision':context.revision,'card':run.answers}

    @app.post('/api/runs/{run_id}/register')
    def save_card(run_id:int,u=Depends(current),s=Depends(db)):
        if u.role!='student': raise HTTPException(403)
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        if not context: raise HTTPException(409)
        if (run.answers.get('no_contact') or run.answers.get('call_lost')) and not run.answers.get('description') and not run.answers.get('classifier_ids'):
            context.processed=True;context.checked=True
            context.scenario_snapshot={**context.scenario_snapshot,'empty_contact':True}
            run.answers={**run.answers,'empty_contact':True,'services':[],'dispatch':[]}
            register_card(s,u,run,context);s.commit();return run_payload(s,run,context)
        if not run.answers.get('description'):raise HTTPException(422,'Заполните описание')
        if not run.answers.get('address') and any(x.get('visible',True) for x in run.answers.get('dispatch',[])):raise HTTPException(422,'Для оповещения реагирующих служб требуется адрес')
        register_card(s,u,run,context);s.commit();return run_payload(s,run,context)

    @app.post('/api/runs/{run_id}/status')
    def set_status(run_id:int,x:StatusIn,u=Depends(current),s=Depends(db)):
        if u.role!='student': raise HTTPException(403)
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        if not context or not context.registered_at: raise HTTPException(409,'Сначала сохраните карточку')
        if x.service_code!=context.scenario_snapshot.get('service_code','112'): raise HTTPException(403,'Можно менять статус только своей учебной службы')
        history=history_for(events_for(s,run));own=history.get(x.service_code,[])
        if not own: raise HTTPException(422,'Службы нет в списке оповещения')
        if x.status not in available_statuses(own[-1]['status'],x.service_code): raise HTTPException(409,'Недопустимый переход статуса')
        if x.status in (REJECTED,REFUSED) and not x.comment.strip(): raise HTTPException(422,'Укажите причину отказа и сведения о передаче информации')
        s.add(CardEvent(run_id=run.id,user_id=u.id,kind='service.status',data=x.model_dump()))
        log(s,u,'service.status',{'run_id':run.id,**x.model_dump()});s.commit();return run_payload(s,run,context)

    @app.post('/api/runs/{run_id}/work-call')
    def add_call(run_id:int,x:WorkCallIn,u=Depends(current),s=Depends(db)):
        if u.role!='student': raise HTTPException(403)
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        if context and context.scenario_snapshot.get('mode')=='dispatch':
            own=history_for(events_for(s,run)).get(context.scenario_snapshot['service_code'],[])
            if own and own[-1]['status'] in TERMINAL: raise HTTPException(409,'Служба завершила работу; редактирование закрыто')
        if not context or not context.registered_at: raise HTTPException(409,'Сначала сохраните карточку')
        version=s.scalar(select(ClassifierVersion).order_by(ClassifierVersion.id.desc()))
        directory_codes=set(SERVICE_NAMES)|({x['code'] for x in version.manifest['columns']} if version else set())
        if x.service and x.service not in directory_codes and x.service not in run.answers.get('services',[]): raise HTTPException(422,'Выберите службу из справочника или заполните «Куда звонили»')
        s.add(CardEvent(run_id=run.id,user_id=u.id,kind='work.call',data=x.model_dump()));log(s,u,'work.call',{'run_id':run.id});s.commit();return run_payload(s,run,context)

    @app.post('/api/runs/{run_id}/processed')
    def processed(run_id:int,u=Depends(current),s=Depends(db)):
        if u.role!='student': raise HTTPException(403)
        run,context=get_run(s,u,run_id,True);ensure_writable(run,context,s)
        if not context or not context.registered_at: raise HTTPException(409)
        if context.scenario_snapshot.get('mode')=='dispatch': raise HTTPException(403,'Отработка оповещения доступна специалисту 112')
        context.processed=True;log(s,u,'card.processed',{'run_id':run.id});s.commit();return run_payload(s,run,context)

    @app.post('/api/runs/{run_id}/review')
    def expert_review(run_id:int,x:ReviewIn,u=Depends(current),s=Depends(db)):
        assert_teacher(u);run,context=get_run(s,u,run_id,True)
        if not run.finished_at: raise HTTPException(409,'Тренировка ещё не завершена')
        review=ExpertReview(run_id=run.id,teacher_id=u.id,score=x.score,comment=x.comment.strip())
        s.add(review);log(s,u,'report.review',{'run_id':run.id,'score':x.score,'comment':x.comment.strip()});s.commit();return {'status':'ok'}

    @app.get('/api/lessons/{lesson_id}/monitor')
    def monitor(lesson_id:int,u=Depends(current),s=Depends(db)):
        assert_teacher(u);lesson=s.get(Lesson,lesson_id)
        if not lesson or lesson.teacher_id!=u.id: raise HTTPException(403)
        names={x.id:x.username for x in s.scalars(select(User).where(User.id.in_(lesson.student_ids)))}
        return [{**run_payload(s,run,s.get(RunContext,run.id)),'student_name':names.get(run.student_id)} for run in s.scalars(select(SessionRun).join(RunContext).where(RunContext.lesson_id==lesson.id).order_by(SessionRun.id.desc()))]
