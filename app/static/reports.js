'use strict';
const reportFieldNames={incident_type:'Тип происшествия',address:'Место происшествия',caller_name:'ФИО заявителя',caller_status:'Статус заявителя',aon:'Номер, с которого звонят',caller_phone:'Обратный номер',on_site_phone:'Телефон на месте',victims_count:'Количество пострадавших',description:'Описание',operator_comment:'Комментарий оператора',victims:'Есть пострадавшие',city:'Город',street:'Улица',house:'Дом',apartment:'Квартира',floor:'Этаж',entrance:'Подъезд',access_code:'Код входа',object_name:'Объект',latitude:'Широта',longitude:'Долгота',country:'Страна',region:'Регион',district:'Район',borough:'Округ',building:'Корпус',structure:'Строение',descriptive_address:'Ориентиры',services:'Службы'};
const reportVoiceNames={irina:'Ирина',denis:'Денис',dmitri:'Дмитрий'};
const reportCallStates={queued:'Готовится озвучка',ringing:'Ожидание ответа',answered:'Разговор',ended:'Завершён',cancelled:'Отменён',failed:'Не состоялся'};
let reportOpenSequence=0;
function reportValue(value){if(value===null||value===undefined||value==='')return 'Не указано';if(Array.isArray(value))return value.map(reportValue).join(', ')||'Не выбрано';if(typeof value==='boolean')return value?'Да':'Нет';return ({yes:'Да',no:'Нет',unknown:'Неизвестно'}[value]??String(value));}
function reportService(run,code){return services.find(s=>s[0]===code)?.[1]||run.card?.dispatch?.find(s=>s.code===code)?.name||directoryCache.find(s=>s.code===code)?.name||code;}
function reportQuestion(group,field){return state.questionnaireCatalog?.find(q=>q.id===group)?.questions.find(q=>q.id===field)?.label||reportFieldNames[field]||({'feature_0':'Первый признак','feature_1':'Второй признак','feature_2':'Третий признак'}[field])||'Дополнительный ответ';}
function reportSection(container,title,open=true){const section=el('details','report-section');section.open=open;section.append(el('summary','',title));container.append(section);return section;}
function reportTable(container,headers,rows){const wrap=el('div','report-table');table(wrap,headers,rows);container.append(wrap);}
function reportEvent(event,run){
  const d=event.data||{};
  const labels={'card.register':'Карточка зарегистрирована','card.skip':'Карточка пропущена','card.assignment_removed':'Обработка закрыта: нет назначения преподавателя','card.resume':'Обработка возобновлена','card.finish':'Тренировка завершена','card.checked':'Карточка проверена','card.supplement':'Карточка дополнена','card.link':'Повторное обращение связано','card.indicator':'Изменился индикатор карточки','card.help':'Запрошена помощь','card.help_ack':'Запрос помощи принят','card.reminder':'Установлено напоминание','card.reminder_clear':'Напоминание закрыто','card.edit_lock':'Открыто дополнение','card.edit_unlock':'Дополнение закрыто','service.status':'Изменение статуса службы','work.call':'Рабочий звонок','sms.incoming':'Входящее SMS','sms.reply':'Ответ на SMS','sms.read':'SMS прочитано','sip.queued':'Звонок поставлен в подготовку','sip.audio_ready':'Озвучка готова','sip.ringing':'Телефон вызван','sip.answered':'Звонок принят','sip.ended':'Разговор завершён','sip.failed':'Ошибка звонка','sip.cancelled':'Звонок отменён','sip.transfer':'Звонок переведён','sip.conference':'Создана учебная конференция'};
  let detail='';
  if(d.service_code)detail=reportService(run,d.service_code);
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
  const url=URL.createObjectURL(await response.blob());const audio=el('audio');audio.setAttribute('aria-label',`Запись звонка №${call.id}`);audio.controls=true;audio.src=url;audio.dataset.reportObjectUrl=url;container.append(audio);await audio.play().catch(()=>{});
}
async function openReportDetail(identifier){
  const sequence=++reportOpenSequence;const container=$('report-detail');
  container.querySelectorAll('[data-report-object-url]').forEach(a=>URL.revokeObjectURL(a.dataset.reportObjectUrl));
  show('report-detail',true);container.replaceChildren(el('p','','Загружаем полный разбор…'));
  const run=await api(`/api/reports/${identifier}`);if(sequence!==reportOpenSequence)return;
  container.replaceChildren();
  const head=el('div','report-heading');const info=el('div');info.append(el('h2','',run.scenario_title),el('p','',`${run.student_name} · попытка №${run.id} · ${run.mode==='dispatch'?'ДДС':'Карточка 112'} · ${run.status}`),el('p','source-meta',`${run.lesson_title} · ${run.card?.channel||'Учебный вызов'}`),el('p','source-meta',`Начало: ${skipDate(run.started_at)} · Завершение: ${run.finished_at?skipDate(run.finished_at):'Ещё идёт'}`));
  const print=el('button','secondary','Печать / PDF');print.type='button';print.addEventListener('click',()=>{const closed=[...container.querySelectorAll('details:not([open])')];closed.forEach(d=>d.open=true);window.print();closed.forEach(d=>d.open=false);});head.append(info,print);container.append(head);
  const timings=el('div','report-timings');for(const [label,value] of [['Создание карточки',duration(run.timings.creation_seconds)],['После регистрации',duration(run.timings.postprocessing_seconds)],['Пропуски',run.timings.skip_count],['Возобновления',run.timings.resume_count]]){const tile=el('div');tile.append(el('small','',label),el('strong','',value));timings.append(tile);}if(run.timings.reaction_seconds!=null){const tile=el('div');tile.append(el('small','','Первая реакция ДДС'),el('strong','',duration(run.timings.reaction_seconds)));timings.append(tile);}container.append(timings);
  const result=el('div','result');result.dataset.fullReport='true';container.append(result);if(run.report)renderResult(result,run.report);else result.append(el('p','','Тренировка ещё идёт. Итоговая оценка появится после завершения.'));
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
  if(run.calls.length){const calls=reportSection(container,'Звонки и записи попыток');for(const call of run.calls){const row=el('div','report-call');row.append(el('b','',`Звонок №${call.id} · ${(call.state==='cancelled'&&call.answered_at?'Завершён оператором':reportCallStates[call.state]||call.state)}`),el('p','source-meta',`Начат ${skipDate(call.created_at)}${call.waiting_seconds!=null?' · До ответа '+call.waiting_seconds+' сек.':''}${call.conversation_seconds!=null?' · Разговор '+call.conversation_seconds+' сек.':''}`));if(call.audio)row.append(el('p','',`Голос: ${reportVoiceNames[call.audio.voice]||call.audio.voice} · Подготовка ${(call.audio.preparation_ms/1000).toFixed(2)} сек. · ${call.audio.cached?'кэш':'новый синтез'}`));if(call.error)row.append(el('p','comparison-different',call.error));if(call.recording_available){const play=el('button','secondary','Прослушать запись');play.addEventListener('click',guarded(async()=>{play.disabled=true;try{await loadReportRecording(run,call,row);}catch(e){play.disabled=false;throw e;}}));row.append(play);}calls.append(row);}}
  const history=reportSection(container,'История действий',false);const meaningful=run.events.filter(e=>!['card.draft','draft.save'].includes(e.kind));reportTable(history,['Когда','Кто','Действие','Подробности'],meaningful.map(e=>reportEvent(e,run)));
  const saved=reportSection(container,'Полная карточка студента',false);const savedBody=el('div');saved.append(savedBody);let cardLoaded=false;saved.addEventListener('toggle',()=>{if(saved.open&&!cardLoaded){cardLoaded=true;renderSavedCard(savedBody,run,false);}});
  if(run.expert_review||(state.role==='teacher'&&run.finished_at&&run.score!==null&&!run.report?.skipped))renderExpertReview(container,run);
  else if(state.role==='teacher')container.append(el('p','source-meta','Экспертная оценка доступна после завершения тренировки с первичной оценкой.'));
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
