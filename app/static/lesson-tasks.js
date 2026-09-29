'use strict';
const lessonTaskLabels={pending:'Не начато',in_progress:'В работе',skipped:'Пропущено',completed:'Выполнено'};
const openLessonTasks=new Set();
let lessonTaskLoading=false;
function activeLessonName(){return state.lessons?.find(lesson=>lesson.id===state.lessonId)?.title||'другого занятия';}
function lessonTaskPanel(lesson,location){
 const panel=el('article','panel lesson-progress');panel.dataset.lessonId=lesson.id;
 const counts=lesson.counts||{total:lesson.scenario_ids.length,completed:0,skipped:0,in_progress:0,pending:lesson.scenario_ids.length};
 const heading=el('div','lesson-progress-heading');const copy=el('div');
 copy.append(el('b','',lesson.title),el('p','source-meta',`${lesson.mode==='dds'?'ДДС · служба '+lesson.service_code:lesson.mode==='dispatch'?'ДДС · служба '+lesson.service_code:'Карточки 112'} · ${({prepared:'Ожидает начала',active:'Занятие идёт',finished:'Занятие завершено'})[lesson.status]}`));
 const next=el('button','primary',lesson.all_completed?'Все задания выполнены':counts.in_progress?'Продолжить карточку →':!counts.pending&&counts.skipped?'Вернуться к пропущенному →':'Следующая карточка →');next.type='button';
 next.disabled=lesson.status!=='active'||lesson.all_completed||lessonTaskLoading||Boolean(state.runId&&state.lessonId!==lesson.id);
 next.addEventListener('click',guarded(()=>nextLesson(lesson.id)));heading.append(copy,next);panel.append(heading);
 if(lesson.all_completed)panel.append(el('p','lesson-completion','Все задания выполнены. Дождитесь разбора преподавателя.'));
 else if(!counts.pending&&!counts.in_progress&&counts.skipped)panel.append(el('p','lesson-completion',`Новых заданий нет. Пропущено: ${counts.skipped}. К ним можно вернуться, пока занятие активно.`));
 const detail=el('details','lesson-task-list');const key=`${location}:${lesson.id}`;detail.open=openLessonTasks.has(key);
 detail.addEventListener('toggle',()=>{if(!detail.isConnected)return;if(detail.open)openLessonTasks.add(key);else openLessonTasks.delete(key);});
 detail.append(el('summary','',`Задания занятия · выполнено ${counts.completed}/${counts.total} · пропущено ${counts.skipped} · в работе ${counts.in_progress} · не начато ${counts.pending}`));
 const list=el('ol');
 // In the DDS queue the current card and newly received cards must stay visible first.
 // Keep the original order inside each state so the display remains stable between refreshes.
 const tasks=[...(lesson.tasks||[])];
 if(lesson.mode==='dds'){
  const order={in_progress:0,pending:1,skipped:2,completed:3};
  tasks.sort((left,right)=>(order[left.status]??4)-(order[right.status]??4));
 }
 for(const task of tasks){
  const row=el('li','lesson-task-row');row.dataset.taskStatus=task.status;row.dataset.scenarioId=task.scenario_id;
  const title=el('div','lesson-task-copy');title.append(el('b','',task.title));
  if(lesson.mode==='dds'&&lesson.status==='active'&&task.status!=='completed'&&task.elapsed_seconds!=null)title.append(el('small','source-meta',`Время с поступления: ${duration(task.elapsed_seconds)} · таймер идёт в очереди`));
  else if(task.finished_at)title.append(el('small','source-meta',`${task.status==='skipped'?'Пропущено':'Завершено'} ${skipDate(task.finished_at)}${task.elapsed_seconds!=null?' · время '+duration(task.elapsed_seconds):''}${task.score!=null?' · '+task.score+'/100':''}`));
  const badge=el('span',`lesson-task-status ${task.status}`,lessonTaskLabels[task.status]);row.append(title,badge);
  const result=task.status==='completed'||(task.status==='skipped'&&lesson.status==='finished');
  const button=el('button','secondary',result?(task.status==='skipped'?'История':'Результат'):task.status==='skipped'?'Продолжить':task.status==='in_progress'?'Открыть':'Начать');button.type='button';
  button.disabled=result?lessonTaskLoading:lesson.status!=='active'||lessonTaskLoading||Boolean(state.runId&&state.runId!==task.run_id);
  button.addEventListener('click',guarded(async()=>{if(state.busy)return;if(result){switchView('reports');await openReportDetail(task.run_id);}else await startLessonTask(lesson.id,task.scenario_id);}));row.append(button);list.append(row);
 }
 detail.append(list);panel.append(detail);return panel;
}
renderAssignedLessons=function(){const target=$('assigned-lessons');target.replaceChildren();if(state.role!=='student'){show('assigned-lessons',false);return;}for(const lesson of state.lessons||[])target.append(lessonTaskPanel(lesson,'training'));show('assigned-lessons',Boolean(state.lessons?.length));};
const renderLessonListBeforeTasks=renderLessonList;
renderLessonList=function(){if(state.role!=='student')return renderLessonListBeforeTasks();const target=$('lesson-list');target.replaceChildren();if(!state.lessons?.length)target.append(el('div','panel empty-table','Занятия пока не назначены.'));for(const lesson of state.lessons||[])target.append(lessonTaskPanel(lesson,'lessons'));};
const restoreRunBeforeLessonTasks=restoreRun;
restoreRun=async function(run){state.lessonId=run.lesson_id;await restoreRunBeforeLessonTasks(run);renderAssignedLessons();renderLessonList();};
const renderMetricsBeforeLessonTasks=renderMetrics;
renderMetrics=function(){renderMetricsBeforeLessonTasks();renderAssignedLessons();renderLessonList();};
async function requestLessonRun(path){if(state.busy)return null;state.busy=true;lessonTaskLoading=true;renderAssignedLessons();renderLessonList();try{return await api(path,{method:'POST'});}finally{state.busy=false;lessonTaskLoading=false;renderAssignedLessons();renderLessonList();}}
async function startLessonTask(lessonId,scenarioId){
 if(state.busy)return;
 if(state.runId){const task=state.lessons?.find(l=>l.id===lessonId)?.tasks.find(t=>t.scenario_id===scenarioId);if(task?.run_id===state.runId){switchView('training');return;}throw Error('Завершите текущую карточку или нажмите «Пропустить карточку».');}
 const run=await requestLessonRun(`/api/lessons/${lessonId}/tasks/${scenarioId}/start`);if(!run)return;await restoreRun(run);await loadLessons();
}
nextLesson=async function(identifier){
 if(state.busy)return;
 if(state.runId){if(state.lessonId===identifier){switchView('training');return;}throw Error(`Сейчас открыта карточка занятия «${activeLessonName()}». Завершите её или нажмите «Пропустить карточку».`);}
 const run=await requestLessonRun(`/api/lessons/${identifier}/next`);if(!run)return;
 if(!run.done){await restoreRun(run);await loadLessons();return;}
 // Check the server before clearing local state: another tab may have opened a card.
 const active=await api('/api/active-run');if(active){await restoreRun(active);await loadLessons();notify('Задания выбранного занятия выполнены, но у вас есть другая открытая карточка.');return;}
 stopTrainingPhone();clearInterval(state.timer);clearDraftCache();Object.assign(state,{runId:null,startedAt:null,lessonId:null,registered:false,selected:null,timerFrozen:false});
 for(const id of ['incident-form','saved-card','call','empty-state'])show(id,false);
 switchView('training');show('result',true);$('result').replaceChildren(el('h2','','Все задания занятия выполнены'),el('p','','Таймер остановлен. Результаты сохранены; дождитесь разбора преподавателя.'));
 $('incident-title').textContent=state.lessons.find(l=>l.id===identifier)?.title||'Занятие';$('run-badge').textContent='Ожидание разбора';await loadLessons();renderMetrics();notify('Все задания занятия выполнены. Дождитесь разбора преподавателя.');
};

// Make progress visible in the scenario list as well as inside the lesson dropdown.
function scenarioLessonTask(scenarioId){
 const assigned=(state.lessons||[]).flatMap(lesson=>(lesson.tasks||[]).filter(task=>task.scenario_id===scenarioId).map(task=>({lesson,task})));
 const active=assigned.filter(item=>item.lesson.status==='active');
 return active.find(item=>item.task.status==='in_progress')||active.find(item=>item.task.status==='pending'||item.task.status==='skipped')||active[0]||assigned[0];
}
const renderScenariosBeforeTaskStatus=renderScenarios;
renderScenarios=function(){
 if(state.role!=='student')return renderScenariosBeforeTaskStatus();
 const query=$('scenario-search').value.toLocaleLowerCase();const target=$('scenarios');target.replaceChildren();
 const list=state.scenarios.filter(s=>(s.title+' '+s.category).toLocaleLowerCase().includes(query));
 // The card currently being processed must remain visible at the top of the list.
 // Keep the original order for all other cards (stable sort).
 const order={in_progress:0,pending:1,skipped:2,completed:3};
 list.sort((left,right)=>{
  const leftStatus=scenarioLessonTask(left.id)?.task.status;
  const rightStatus=scenarioLessonTask(right.id)?.task.status;
  return (order[leftStatus]??4)-(order[rightStatus]??4);
 });
 if(!list.length){target.append(el('p','empty-table',query?'По вашему поиску заданий нет.':'Нет назначенных заданий. Дождитесь назначения преподавателя или откройте «Занятия».'));return;}
 for(const scenario of list){
  const item=scenarioLessonTask(scenario.id);const completed=item?.task.status==='completed';
  const card=el('div',`scenario scenario-task${completed?' completed':''}${state.selected?.id===scenario.id?' active':''}`);card.dataset.scenarioId=scenario.id;
  const top=el('div','scenario-top');top.append(el('span','category-tag',scenario.category),el('span','scenario-number',`#${String(scenario.id).padStart(3,'0')}`));card.append(top,el('b','',scenario.title.replace(/^Учебный вызов:\s*/i,'')));
  if(item){card.append(el('span',`lesson-task-status ${item.task.status}`,`${completed?'✓ ':''}${lessonTaskLabels[item.task.status]}`),el('small','',`Занятие: ${item.lesson.title}`));}
  else card.append(el('small','','Самостоятельная практика'));
  const actions=el('div','scenario-task-actions');const start=el('button','secondary',completed?'Выполнено':item?.task.status==='skipped'?'Продолжить':item?.task.status==='in_progress'?'Открыть':'Начать');start.type='button';
  start.disabled=completed||Boolean(item&&item.lesson.status!=='active')||Boolean(state.runId&&state.runId!==item?.task.run_id);
  start.addEventListener('click',guarded(async()=>{if(state.busy)return;if(item)await startLessonTask(item.lesson.id,scenario.id);else choose(scenario);}));actions.append(start);
  if(completed){const result=el('button','secondary','Результат');result.type='button';result.addEventListener('click',guarded(async()=>{if(state.busy)return;switchView('reports');await openReportDetail(item.task.run_id);}));actions.append(result);card.append(el('small','scenario-completed-note','Обработка завершена · повторный запуск заблокирован'));}
  card.append(actions);target.append(card);
 }
};
const chooseBeforeCompletedGuard=choose;
choose=function(scenario){const item=state.role==='student'?scenarioLessonTask(scenario.id):null;if(item?.task.status==='completed'){notify('Задание уже выполнено. Откройте «Результат» в списке заданий или сценариев.');return;}chooseBeforeCompletedGuard(scenario);};
const renderResultBeforeCompletedGuard=renderResult;
renderResult=function(container,report,actions=false){renderResultBeforeCompletedGuard(container,report,actions);if(state.role==='student'&&!report.skipped&&(state.lessons||[]).some(lesson=>(lesson.tasks||[]).some(task=>task.run_id===report.run_id))){for(const button of container.querySelectorAll('.result-actions button'))if(button.textContent.includes('Повторить тренировку')){button.disabled=true;button.textContent='Задание выполнено';button.title='Повторный запуск в этом занятии заблокирован';}}};
const renderAssignedBeforeScenarioStatus=renderAssignedLessons;
renderAssignedLessons=function(){renderAssignedBeforeScenarioStatus();if(state.role==='student')renderScenarios();};

$('scenario-search').removeEventListener('input',renderScenariosBeforeTaskStatus);
$('scenario-search').addEventListener('input',renderScenarios);
