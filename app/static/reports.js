'use strict';
const reportFieldNames={incident_type:'Тип происшествия',address:'Место происшествия',caller_name:'ФИО заявителя',caller_status:'Статус заявителя',aon:'Номер, с которого звонят',caller_phone:'Обратный номер',on_site_phone:'Телефон на месте',victims_count:'Количество пострадавших',description:'Описание',operator_comment:'Комментарий оператора',victims:'Есть пострадавшие',city:'Город',street:'Улица',house:'Дом',apartment:'Квартира',floor:'Этаж',entrance:'Подъезд',access_code:'Код входа',object_name:'Объект',latitude:'Широта',longitude:'Долгота',country:'Страна',region:'Регион',district:'Район',borough:'Округ',building:'Корпус',structure:'Строение',descriptive_address:'Ориентиры',services:'Службы'};
const reportVoiceNames={irina:'Ирина',denis:'Денис',dmitri:'Дмитрий'};
const reportCallStates={queued:'Готовится озвучка',ringing:'Ожидание ответа',answered:'Разговор',ended:'Завершён',cancelled:'Отменён',failed:'Не состоялся'};
let reportOpenSequence=0;
function reportValue(value){if(value===null||value===undefined||value==='')return 'Не указано';if(Array.isArray(value))return value.map(reportValue).join(', ')||'Не выбрано';if(typeof value==='boolean')return value?'Да':'Нет';return ({yes:'Да',no:'Нет',unknown:'Неизвестно'}[value]??String(value));}
function reportService(run,code){return state.classifier?.services?.[code]||services.find(s=>s[0]===code)?.[1]||run.card?.dispatch?.find(s=>s.code===code)?.name||directoryCache.find(s=>s.code===code)?.name||code;}
function reportQuestion(group,field){return state.questionnaireCatalog?.find(q=>q.id===group)?.questions.find(q=>q.id===field)?.label||reportFieldNames[field]||({'feature_0':'Первый признак','feature_1':'Второй признак','feature_2':'Третий признак'}[field])||'Дополнительный ответ';}
function reportSection(container,title,open=true){const section=el('details','report-section');section.open=open;section.append(el('summary','',title));container.append(section);return section;}
function reportTable(container,headers,rows){const wrap=el('div','report-table');table(wrap,headers,rows);container.append(wrap);}
function reportEvent(event,run){
  const d=event.data||{};
  const labels={'card.register':'Карточка зарегистрирована','card.skip':'Карточка пропущена','card.assignment_removed':'Обработка закрыта: нет назначения преподавателя','card.resume':'Обработка возобновлена','card.finish':'Тренировка завершена','card.checked':'Карточка проверена','card.supplement':'Карточка дополнена','card.link':'Повторное обращение связано','card.indicator':'Изменился индикатор карточки','card.help':'Запрошена помощь','card.help_ack':'Запрос помощи принят','card.reminder':'Установлено напоминание','card.reminder_clear':'Напоминание закрыто','card.edit_lock':'Открыто дополнение','card.edit_unlock':'Дополнение закрыто','service.status':'Изменение статуса службы','work.call':'Рабочий звонок','sms.incoming':'Входящее SMS','sms.reply':'Ответ на SMS','sms.read':'SMS прочитано','sip.queued':'Звонок поставлен в подготовку','sip.audio_ready':'Озвучка готова','sip.ringing':'Телефон вызван','sip.answered':'Звонок принят','sip.ended':'Разговор завершён','sip.failed':'Ошибка звонка','sip.cancelled':'Звонок отменён','sip.transfer':'Звонок переведён','sip.conference':'Создана учебная конференция','dds.received':'Карточка поступила в ДДС','dds.validation':'Проверка и исправления ДДС','dds.handoff':'Передача информации службе','dds.call.prepared':'Подготовлен исходящий SIP-вызов ДДС'};
  let detail='';
  if(d.service_code)detail=reportService(run,d.service_code);
  if(d.service)detail=reportService(run,d.service);
  if(d.verdict)detail+=' · '+({correct:'Карточка верна',corrected:'Ошибки исправлены',clarification:'Требуется уточнение'}[d.verdict]||d.verdict);
  if(d.findings)detail+=' · '+d.findings;
  if(d.transport)detail+=' · '+d.transport;
  if(d.receiver)detail+=' · Получатель: '+d.receiver;
  if(d.message)detail+=' · '+d.message;
  if(d.outcome)detail+=' · '+({accepted:'Принято',unavailable:'Не удалось связаться',rejected:'Отказ'}[d.outcome]||d.outcome);
  if(d.status)detail+=" · "+d.status;
  if(d.comment)detail+=" · "+d.comment;
  if(d.unit_number)detail+=" · Наряд "+d.unit_number;
  if(d.elapsed_seconds!==undefined)detail+=" · Накопленное время "+duration(d.elapsed_seconds);
  if(d.score!==undefined)detail+=" · "+d.score+'/100';
  if(d.text)detail+=" · "+d.text;
  if(d.error)detail+=" · "+d.error;
  if(d.parent_id)detail+=" · Главная карточка №"+d.parent_id;
  if(d.extension)detail+=" · Учебный номер "+d.extension;
  if(event.kind==='sip.audio_ready')detail=`Голос ${reportVoiceNames[d.voice]||d.voice||'не сохранён'} · ${(d.preparation_ms/1000).toFixed(2)} сек. · ${d.cached?'из кэша':'новый синтез'}`;
  if(event.kind==='card.indicator')detail=`${reportValue(d.before)} → ${reportValue(d.after)}`;
  const node=el('div','',detail.replace(/^ · /,''));
  if(event.kind==='card.supplement')for(const [key,value] of Object.entries(d.after||{}))node.append(el('p','',`${reportFieldNames[key]||'Поле карточки'}: ${reportValue(d.before?.[key])} → ${reportValue(value)}`));
  return [skipDate(event.at),event.actor||'Система',labels[event.kind]||'Изменение карточки',node];
}
async function loadReportRecording(run,call,container){
  const response=await fetch(`/api/telephony/runs/${run.id}/recording?call_id=${call.id}`,{headers:{Authorization:'Bearer '+state.token}});
  if(!response.ok)throw Error('Запись недоступна или ещё сохраняется');
  const url=URL.createObjectURL(await response.blob());const audio=el('audio');audio.setAttribute('aria-label',`Запись разговора студента со службой ${call.service_name||reportService(run,call.service)||'112'}, звонок №${call.id}`);audio.controls=true;audio.src=url;audio.dataset.reportObjectUrl=url;const download=el('a','secondary','Скачать запись ↓');download.href=url;download.download=`dds-${run.id}-call-${call.id}.wav`;container.append(audio,download);await audio.play().catch(()=>{});
}
function closeReportDetail(){++reportOpenSequence;const panel=$('report-detail');panel.querySelectorAll('audio').forEach(audio=>{audio.pause();if(audio.dataset.reportObjectUrl)URL.revokeObjectURL(audio.dataset.reportObjectUrl);});panel.replaceChildren();show('report-detail',false);reportListing(true);$('reports-list').scrollIntoView({behavior:'instant',block:'start'});}
function reportListing(visible){$('reports-list').hidden=!visible;reportFilters.hidden=!visible;reportSummary.hidden=!visible;}
function jumpReportSection(section){section.open=true;section.scrollIntoView({behavior:'smooth',block:'start'});}
function renderReportCalls(container,run){
 const section=reportSection(container,`Звонки и записи разговоров · ${run.calls.length}`);section.id='report-calls';
 section.append(el('p','source-meta','Откройте запись, чтобы услышать разговор студента и учебной службы.'));
 if(!run.calls.length)section.append(el('p','empty-table',run.mode==='dds'?'Студент не совершал SIP-вызовы по этой карточке. Текстовые передачи не являются звонками.':'Звонков по этой карточке нет.'));
 for(const call of run.calls){const row=el('article','report-call');row.dataset.callId=call.id;const name=call.service_name||(call.service?reportService(run,call.service):'Служба 112');
 row.append(el('h3','',call.direction==='outbound'?`Студент → ${name}`:`Заявитель → ${name}`),el('p','source-meta',`Звонок №${call.id}${call.extension?' · Учебный номер '+call.extension:''} · ${call.state==='cancelled'&&call.answered_at?'Завершён оператором':reportCallStates[call.state]||call.state}`),el('p','source-meta',`Начало: ${skipDate(call.created_at)}${call.answered_at?' · Ответ: '+skipDate(call.answered_at):''}${call.ended_at?' · Окончание: '+skipDate(call.ended_at):''}${call.conversation_seconds!=null?' · Разговор: '+duration(call.conversation_seconds):''}`));
 for(const item of call.handoffs||[])row.append(el('p','',`Получатель: ${item.receiver||'Не указан'} · ${({accepted:'Информация принята',unavailable:'Не удалось связаться',rejected:'Отказ'})[item.outcome]||item.outcome}`),el('p','report-text',item.message||''));
 if(call.direction==='outbound'&&!call.handoffs?.length)row.append(el('p','source-meta','Результат передачи студент не зафиксировал.'));
 if(call.error)row.append(el('p','comparison-different',call.error));
 if(call.recording_available){const play=el('button','primary','▶ Прослушать разговор студента');play.type='button';play.addEventListener('click',guarded(async()=>{play.disabled=true;play.textContent='Загружаем запись…';try{await loadReportRecording(run,call,row);play.hidden=true;}catch(error){play.disabled=false;play.textContent='▶ Повторить загрузку записи';throw error;}}));row.append(play);}else row.append(el('p','source-meta',['queued','ringing','answered'].includes(call.state)?'Запись будет доступна после завершения разговора.':call.answered_at?'Аудиозапись не найдена.':'Соединение не состоялось, записи разговора нет.'));
 section.append(row);}return section;
}
async function openReportDetail(identifier){
  const sequence=++reportOpenSequence;const container=$('report-detail');
  container.querySelectorAll('[data-report-object-url]').forEach(a=>URL.revokeObjectURL(a.dataset.reportObjectUrl));
  show('report-detail',true);reportListing(false);container.replaceChildren(el('p','','Загружаем полный разбор…'));
  let run;try{run=await api(`/api/reports/${identifier}`);}catch(error){if(sequence===reportOpenSequence){show('report-detail',false);reportListing(true);}throw error;}if(sequence!==reportOpenSequence)return;
  container.replaceChildren();
  const head=el('div','report-heading');const info=el('div');info.append(el('h2','',run.scenario_title),el('p','',`${run.student_name} · попытка №${run.id} · ${['dispatch','dds'].includes(run.mode)?'ДДС':'Карточка 112'} · ${run.status}`),el('p','source-meta',`${run.lesson_title} · ${run.card?.channel||'Учебный вызов'}`),el('p','source-meta',`Начало: ${skipDate(run.started_at)} · Завершение: ${run.finished_at?skipDate(run.finished_at):'Ещё идёт'}`));
  const print=el('button','secondary','Печать / PDF');print.type='button';print.addEventListener('click',()=>{const closed=[...container.querySelectorAll('details:not([open])')];closed.forEach(d=>d.open=true);window.print();closed.forEach(d=>d.open=false);});head.append(info,print);container.append(head);
 const navigation=el('div','report-detail-nav');const back=el('button','secondary','← К списку результатов');back.textContent='Закрыть без сохранения';back.addEventListener('click',closeReportDetail);navigation.append(back);for(const [label,id] of [['Звонки и записи','report-calls'],['Оценка и исправления','report-score'],['История действий','report-history']]){const button=el('button','secondary',label);button.addEventListener('click',()=>{const target=$(id);if(target){if(target.tagName==='DETAILS')target.open=true;target.scrollIntoView({behavior:'smooth',block:'start'});}});navigation.append(button);}container.append(navigation);renderReportCalls(container,run);

  const timings=el('div','report-timings');for(const [label,value] of [[run.mode==='dds'?'Обработка карточки ДДС':'Создание карточки',duration(run.timings.creation_seconds)],['После регистрации',duration(run.timings.postprocessing_seconds)],['Пропуски',run.timings.skip_count],['Возобновления',run.timings.resume_count]]){const tile=el('div');tile.append(el('small','',label),el('strong','',value));timings.append(tile);}if(run.timings.reaction_seconds!=null){const tile=el('div');tile.append(el('small','','Первая реакция ДДС'),el('strong','',duration(run.timings.reaction_seconds)));timings.append(tile);}container.append(timings);
  const result=el('div','result');result.id='report-score';result.dataset.fullReport='true';container.append(result);if(run.report)renderResult(result,run.report);else result.append(el('p','','Тренировка ещё идёт. Итоговая оценка появится после завершения.'));
  if(run.report?.dds?.missing_call_services?.length)result.append(el('p','comparison-different','Нет подтверждённого завершённого звонка: '+run.report.dds.missing_call_services.map(c=>reportService(run,c)).join(', ')));
  if(run.report?.dds)result.append(el('p','source-meta',run.report.dds.require_sip?'Условие занятия: SIP-звонки учитываются в оценке. '+(run.report.dds.pending_sip_services?.length?'Не подтверждены передачи: '+run.report.dds.pending_sip_services.map(c=>reportService(run,c)).join(', '):'Все требуемые передачи подтверждены звонками.'):'Условие занятия: разрешена текстовая симуляция передачи.'));

  if(run.caller_text){const source=reportSection(container,'Исходное обращение заявителя');source.append(el('p','report-text',run.caller_text));}
  if(run.comparisons){const compare=reportSection(container,'Ответы студента и эталон');compare.append(el('p','source-meta','Сравнение полей справочное: формулировки могут отличаться. Баллы и ошибки рассчитаны по критериям выше.'));reportTable(compare,['Поле','Ответ студента','Эталон','Сравнение'],run.comparisons.map(c=>[c.label,c.field==='services'?(c.actual||[]).map(v=>reportService(run,v)).join(', ')||'Не выбрано':reportValue(c.actual),c.status==='no_reference'?'Эталон не задан':c.field==='services'?(c.expected||[]).map(v=>reportService(run,v)).join(', '):reportValue(c.expected),el('span','comparison-'+c.status,{match:'Совпадает',different:'Есть отличия',no_reference:'Без эталона'}[c.status])]));
    for(const [field,label] of [['description','Описание студента'],['operator_comment','Комментарий студента']])compare.append(el('h3','',label),el('p','report-text',run.card?.[field]||'Не заполнено'));
    compare.append(el('h3','','Ключевые сведения эталона'),el('p','report-text',run.expected?.operator_comment||'Не заданы'));
    if(run.service_difference.missing.length)compare.append(el('p','comparison-different','Не выбраны службы: '+run.service_difference.missing.map(c=>reportService(run,c)).join(', ')));
    if(run.service_difference.extra.length)compare.append(el('p','source-meta','Дополнительно выбраны: '+run.service_difference.extra.map(c=>reportService(run,c)).join(', ')));
  }
  const groups=new Set([...Object.keys(run.card?.questionnaire_answers||{}),...Object.keys(run.expected?.questionnaire_answers||{})]);
  if(groups.size){const answers=reportSection(container,'Все ответы опросных карт');for(const group of groups){answers.append(el('h3','',state.questionnaireCatalog?.find(q=>q.id===group)?.title||'Опросная карта'));const actual=run.card?.questionnaire_answers?.[group]||{},expected=run.expected?.questionnaire_answers?.[group]||{};const fields=new Set([...Object.keys(actual),...Object.keys(expected)]);reportTable(answers,['Вопрос','Ответ студента','Эталон'],[...fields].map(f=>[reportQuestion(group,f),reportValue(actual[f]),Object.hasOwn(expected,f)?reportValue(expected[f]):'Эталон не задан']));}}
  const statuses=reportSection(container,'Реагирование служб');let hasStatus=false;for(const [code,history] of Object.entries(run.service_history||{})){hasStatus=true;statuses.append(el('h3','',reportService(run,code)));reportTable(statuses,['Время','Статус','Наряд','Комментарий'],history.map(x=>[skipDate(x.at),x.status,x.unit_number||'—',x.comment||'—']));}if(!hasStatus)statuses.append(el('p','','Статусы служб не записаны.'));
  if(run.card?.excluded_services?.length)statuses.append(el('p','',`Службы исключены вручную: ${run.card.excluded_services.map(c=>reportService(run,c)).join(', ')}. Причина: ${run.card.service_override_reason||'Не сохранена'}`));

  const history=reportSection(container,'История действий',false);history.id='report-history';const meaningful=run.events.filter(e=>!['card.draft','draft.save'].includes(e.kind));reportTable(history,['Когда','Кто','Действие','Подробности'],meaningful.map(e=>reportEvent(e,run)));
  const saved=reportSection(container,'Полная карточка студента',false);const savedBody=el('div');saved.append(savedBody);let cardLoaded=false;saved.addEventListener('toggle',()=>{if(saved.open&&!cardLoaded){cardLoaded=true;renderSavedCard(savedBody,run,false);}});
  if(run.expert_review||(state.role==='teacher'&&run.finished_at&&run.score!==null&&!run.report?.skipped))renderExpertReview(container,run);
  else if(state.role==='teacher')container.append(el('p','source-meta','Экспертная оценка доступна после завершения тренировки с первичной оценкой.'));
  if(!container.querySelector('.expert-box form')){const close=el('button','secondary','Закрыть');close.type='button';close.addEventListener('click',closeReportDetail);container.append(close);}
  container.scrollIntoView({behavior:'smooth',block:'start'});
}
const renderResultBeforeFullReport=renderResult;
renderResult=function(container,report,actions=false){
  renderResultBeforeFullReport(container,report,actions);
  if(['teacher','admin'].includes(state.role)&&report.run_id&&container.dataset.fullReport!=='true'){
    const detail=el('button','secondary','Ответы, история и записи →');
    detail.addEventListener('click',guarded(async()=>{switchView('reports');await openReportDetail(report.run_id);}));container.append(detail);
  }
};

// Dates are local calendar days; the API receives an exclusive end in UTC.
const reportFilters=el('div','panel report-filters');
function reportControl(label,node){const wrap=el('label');wrap.append(el('span','',label),node);reportFilters.append(wrap);return node;}
const reportGrouping=reportControl('Группировка',el('select'));
for(const [value,label] of [['student','По студентам'],['day','По датам'],['none','Общий список']]){const option=el('option','',label);option.value=value;reportGrouping.append(option);}
const reportStudent=reportControl('Студент',el('select'));
const reportFrom=reportControl('С даты',el('input'));reportFrom.type='date';
const reportTo=reportControl('По дату включительно',el('input'));reportTo.type='date';
const reportReset=el('button','secondary','Сбросить фильтры');reportReset.type='button';reportFilters.append(reportReset);
const reportSummary=el('p','source-meta');
$('reports-list').before(reportFilters,reportSummary);
let reportFilterOwner=null;
function reportLocalDay(value){const d=new Date(value);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
function reportPeriod(){
  if(reportFrom.value&&reportTo.value&&reportFrom.value>reportTo.value)throw Error('Дата начала должна быть не позже даты окончания');
  const params=new URLSearchParams();
  if(reportFrom.value)params.set('started_from',new Date(reportFrom.value+'T00:00:00').toISOString());
  if(reportTo.value){const end=new Date(reportTo.value+'T00:00:00');end.setDate(end.getDate()+1);params.set('started_before',end.toISOString());}
  if(reportStudent.value)params.set('student_id',reportStudent.value);
  return params;
}
function reportFilteredRows(){reportPeriod();return state.reports.filter(r=>(!reportStudent.value||String(r.student_id)===reportStudent.value)&&(!reportFrom.value||reportLocalDay(r.started_at)>=reportFrom.value)&&(!reportTo.value||reportLocalDay(r.started_at)<=reportTo.value));}
function reportStats(rows){const graded=rows.filter(r=>!r.report?.skipped&&r.score!==null&&r.score!==undefined);const completed=rows.filter(r=>r.finished_at&&!r.report?.skipped).length;return `${rows.length} попыток · завершено ${completed} · пропущено ${rows.filter(r=>r.report?.skipped).length} · средний балл ${graded.length?(graded.reduce((sum,r)=>sum+r.score,0)/graded.length).toFixed(1):'—'}`;}
renderReports=function(){
  const owner=state.role+':'+(state.username||$('who').textContent);
  if(reportFilterOwner!==owner){reportFilterOwner=owner;reportFrom.value='';reportTo.value='';reportStudent.replaceChildren();reportGrouping.value=state.role==='student'?'day':'student';show('report-detail',false);reportListing(true);}
  const selected=reportStudent.value;reportStudent.replaceChildren(el('option','','Все студенты'));reportStudent.firstChild.value='';
  const students=new Map(state.reports.map(r=>[String(r.student_id),r.student_name||`Студент №${r.student_id}`]));
  for(const [id,name] of [...students].sort((a,b)=>a[1].localeCompare(b[1],'ru'))){const option=el('option','',name);option.value=id;reportStudent.append(option);}
  reportStudent.value=students.has(selected)?selected:'';reportStudent.parentElement.hidden=state.role==='student';
  const container=$('reports-list');const opened=new Set([...container.querySelectorAll('details[open][data-report-group]')].map(g=>g.dataset.reportGroup));container.replaceChildren();
  let rows;try{rows=reportFilteredRows();}catch(e){reportSummary.textContent=e.message;return;}
  reportSummary.textContent=reportStats(rows)+' · период по дате начала обработки';
  if(!rows.length){container.append(el('p','empty-table','По выбранным фильтрам результатов нет.'));return;}
  function appendRows(target,items){const holder=el('div','report-table');target.append(holder);table(holder,['Сценарий','Студент','Начало','Результат','Время','Отчёт'],items.map(run=>{const title=el('span','',run.scenario_title||`Сценарий №${run.scenario_id}`);title.append(el('small','',`Попытка №${run.id}`));const score=el('span','score-pill'+(run.score!==null&&run.score<60?' low':''),run.report?.skipped?'Пропущена':run.score===null?'В процессе':`${run.score} / 100`);const button=el('button','table-button','Разбор →');button.addEventListener('click',guarded(()=>openReportDetail(run.id)));return [title,run.student_name||`№${run.student_id}`,date(run.started_at),score,run.report?duration(run.report.elapsed_seconds):'—',button];}));}
  if(reportGrouping.value==='none'){appendRows(container,rows);return;}
  const groups=new Map();for(const row of rows){const key=reportGrouping.value==='day'?reportLocalDay(row.started_at):String(row.student_id);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}
  const entries=[...groups];entries.sort((a,b)=>reportGrouping.value==='day'?b[0].localeCompare(a[0]):(a[1][0].student_name||'').localeCompare(b[1][0].student_name||'','ru'));
  for(const [key,items] of entries){const group=el('details','report-result-group');group.dataset.reportGroup=owner+':'+reportGrouping.value+':'+key;group.open=entries.length===1||opened.has(group.dataset.reportGroup);const heading=el('summary');const title=reportGrouping.value==='day'?new Date(key+'T12:00:00').toLocaleDateString('ru-RU',{day:'numeric',month:'long',year:'numeric'}):items[0].student_name||`Студент №${key}`;heading.append(el('strong','',title),el('span','source-meta',reportStats(items)));group.append(heading);container.append(group);appendRows(group,items);}
};
for(const control of [reportGrouping,reportStudent,reportFrom,reportTo])control.addEventListener('change',()=>{show('report-detail',false);reportListing(true);renderReports();});
reportReset.addEventListener('click',()=>{reportStudent.value='';reportFrom.value='';reportTo.value='';renderReports();});
$('export-reports').addEventListener('click',guarded(async()=>{
  const params=reportPeriod();const button=$('export-reports');button.disabled=true;
  try{const response=await fetch('/api/reports/export.csv?'+params,{headers:{Authorization:'Bearer '+state.token}});if(!response.ok)throw Error('Не удалось выгрузить отчёт');const url=URL.createObjectURL(await response.blob());const link=el('a');link.href=url;link.download=`arm112-results_${reportFrom.value||'начало'}_${reportTo.value||'сегодня'}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}finally{button.disabled=false;}
}));
