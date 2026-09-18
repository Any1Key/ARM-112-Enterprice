"""Teacher evidence from immutable scenario snapshots and audited student actions."""
from fastapi import Depends
from sqlalchemy import select
from app.models import User,Scenario,Lesson,ExpertReview,VoipCall,CardEvent
from app.workflows import get_run,run_payload,aware
from app.recordings import recording_files

FIELDS=[('incident_type','Тип происшествия'),('address','Место происшествия'),
        ('caller_name','ФИО заявителя'),('caller_status','Статус заявителя'),
        ('aon','Номер, с которого звонят'),('caller_phone','Обратный номер'),
        ('on_site_phone','Телефон на месте'),('services','Службы реагирования'),
        ('victims_count','Количество пострадавших')]

def register_report_details(app,db,current):
    @app.get('/api/reports/{run_id}')
    def details(run_id:int,u=Depends(current),s=Depends(db)):
        run,context=get_run(s,u,run_id)
        payload=run_payload(s,run,context)
        snapshot=context.scenario_snapshot if context else {}
        scenario=s.get(Scenario,run.scenario_id)
        expected=snapshot.get('expected',scenario.expected if scenario else {})
        student=s.get(User,run.student_id)
        lesson=s.get(Lesson,context.lesson_id) if context and context.lesson_id else None
        review=s.scalar(select(ExpertReview).where(ExpertReview.run_id==run.id).order_by(ExpertReview.id.desc()))
        events=list(s.scalars(select(CardEvent).where(CardEvent.run_id==run.id).order_by(CardEvent.at,CardEvent.id)))
        names={x.id:x.username for x in s.scalars(select(User).where(User.id.in_({e.user_id for e in events if e.user_id}))) }
        calls=list(s.scalars(select(VoipCall).where(VoipCall.run_id==run.id).order_by(VoipCall.id)))
        prepared={e.data.get('call_id'):e.data for e in events if e.kind=='dds.call.prepared'}
        from app.dds import directory
        service_names=directory(s) if prepared else {}
        handoffs={c.id:[e.data for e in events if e.kind=='dds.handoff' and e.data.get('call_id')==c.id] for c in calls}
        audio={e.data.get('call_id'):e.data for e in events if e.kind=='sip.audio_ready'}
        files=recording_files(s,run.id,calls)
        result={**payload,'id':run.id,'student_name':student.username if student else 'Удалённый пользователь',
                'scenario_title':snapshot.get('title',scenario.title if scenario else ''),
                'lesson_title':lesson.title if lesson else 'Самостоятельная практика / назначенное SMS',
                'expert_review':{'score':review.score,'comment':review.comment,'at':aware(review.at)} if review else None,
                'events':[{**e,'actor':names.get(e['user_id'],'Система')} for e in payload['events']],
                'timings':{'creation_seconds':payload['elapsed_seconds'],'postprocessing_seconds':payload['postprocessing_seconds'],
                           'reaction_seconds':(run.report or {}).get('reaction_seconds'),
                           'skip_count':sum(e.kind=='card.skip' for e in events),
                           'resume_count':sum(e.kind=='card.resume' for e in events)},
                'calls':[{'id':c.id,'direction':'outbound' if c.id in prepared else 'inbound',
                          'service':prepared.get(c.id,{}).get('service'), 'service_name':prepared.get(c.id,{}).get('name') or service_names.get(prepared.get(c.id,{}).get('service')),
                          'extension':prepared.get(c.id,{}).get('extension'), 'handoffs':handoffs[c.id], 'state':c.state,'created_at':aware(c.created_at),'answered_at':aware(c.answered_at),
                          'ended_at':aware(c.ended_at),'error':c.error,'audio':audio.get(c.id),
                          'waiting_seconds':max(0,round((c.answered_at-c.created_at).total_seconds(),2)) if c.answered_at else None,
                          'conversation_seconds':max(0,round((aware(c.ended_at)-aware(c.answered_at)).total_seconds(),2)) if c.answered_at and c.ended_at else None,
                          'recording_available':c.id in files and c.state not in ('queued','ringing','answered')} for c in calls],
                'recording_available':bool(files and not any(c.state in ('queued','ringing','answered') for c in calls))}
        # Active students can see their own evidence, but never the scenario answer key.
        if u.role in ('teacher','admin'):
            if snapshot.get('mode')=='dds':result['dds_reference_card']=expected.get('dds_gold')
            card=run.answers or {}
            comparisons=[]
            for field,label in FIELDS:
                target=expected.get(field);actual=card.get(field)
                defined=target is not None and target!='' and (field!='services' or bool(target))
                if field=='services':match=set(target or [])==set(actual or [])
                else:match=str('' if target is None else target).strip().casefold()==str('' if actual is None else actual).strip().casefold()
                comparisons.append({'field':field,'label':label,'expected':target,'actual':actual,
                                    'status':'no_reference' if not defined else 'match' if match else 'different'})
            result.update(expected=expected,caller_text=snapshot.get('caller_text',scenario.caller_text if scenario else ''),comparisons=comparisons,
                          service_difference={'missing':sorted(set(expected.get('services',[]))-set(card.get('services',[]))),
                                              'extra':sorted(set(card.get('services',[]))-set(expected.get('services',[])))})
        return result
