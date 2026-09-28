"""Service transitions and card-level indicators from the ARM-112 memo, pp. 21–32."""
from datetime import timedelta

ACCEPTED='Принята'
REJECTED='Не принята'
COMPLETE='Работы завершены'
REFUSED='Отказ от выполнения работ'
NO_CREW='Работы завершены: Завершение работ без бригады'
PROGRESS=['Начало реагирования','Прибытие','Проведение работ']
TERMINAL={COMPLETE,REFUSED,NO_CREW}
DDS_REQUIRED=[ACCEPTED,*PROGRESS,COMPLETE]

def dds_status_options(current,service):
    """The confirmed DDS exercise requires each work stage in order."""
    if current in (REJECTED,COMPLETE,REFUSED,NO_CREW):return []
    if current in ('Добавлена','Получена службой',None):return [ACCEPTED,REJECTED]
    if current==ACCEPTED:return [PROGRESS[0],REFUSED]+([NO_CREW] if service=='103' else [])
    if current in PROGRESS:return [DDS_REQUIRED[DDS_REQUIRED.index(current)+1],REFUSED]
    return []

def dds_status_complete(statuses,service):
    if not statuses:return False
    if statuses==[REJECTED]:return True
    if statuses[0]!=ACCEPTED:return False
    if statuses[-1]==REFUSED:return True
    if service=='103' and statuses==[ACCEPTED,NO_CREW]:return True
    return statuses==DDS_REQUIRED

def available_statuses(current,service):
    if current in TERMINAL: return []
    if current in ('Добавлена','Получена службой',None):
        return [ACCEPTED,NO_CREW] if service=='103' else [ACCEPTED,REJECTED]
    if current==REJECTED: return [ACCEPTED]
    # The memo allows picking a known work stage after acceptance; it does not
    # require fabricating all preceding stages. Never allow moving backwards.
    remaining=PROGRESS[PROGRESS.index(current)+1:] if current in PROGRESS else PROGRESS
    return remaining+[COMPLETE,NO_CREW if service=='103' else REFUSED]

def card_indicator(context,service_history,now):
    if not context or not context.registered_at: return 'Черновик'
    if getattr(context,'scenario_snapshot',{}).get('empty_contact'): return 'Завершена'
    latest={code:events[-1]['status'] for code,events in service_history.items() if events}
    if not latest or all(status in (COMPLETE,NO_CREW) for status in latest.values()): return 'Завершена'
    if context.checked and any(status in (REJECTED,REFUSED) for status in latest.values()): return 'Отказ'
    if now-context.registered_at>timedelta(hours=48): return 'Не завершено'
    if context.checked: return 'Проверена'
    # In the 112 training mode the operator confirms the alerting action with
    # the explicit "Оповещение завершено" button; service-stage statuses are
    # used by the separate dispatch mode. Do not turn a completed training
    # action back into an overdue alert merely because 30 seconds elapsed.
    if context.processed: return 'Отработана'
    if any(status in ('Добавлена','Получена службой') for status in latest.values()) and now-context.registered_at>timedelta(seconds=30): return 'Не оповещено'
    return 'Зарегистрирована'
