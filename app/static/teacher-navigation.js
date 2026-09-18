'use strict';
const teacherMenu=document.querySelector('.sidebar nav');
const originalMenu=[...teacherMenu.children];
const originalMenuLabels=new Map(originalMenu.map(button=>[button,button.innerHTML]));
let teacherNavigationOwner='';
function teacherMenuGroup(title,views){teacherMenu.append(el('div','teacher-menu-group',title));for(const view of views){const button=originalMenu.find(b=>b.dataset.view===view);if(button)teacherMenu.append(button);}}
function configureTeacherNavigation(){
 const owner=state.role+':'+state.username;if(teacherNavigationOwner===owner)return;teacherNavigationOwner=owner;
 for(const [button,label] of originalMenuLabels)button.innerHTML=label;teacherMenu.replaceChildren(...originalMenu);
 document.querySelector('.nav-caption').textContent=state.role==='teacher'?'КАБИНЕТ ПРЕПОДАВАТЕЛЯ':'РАБОЧЕЕ ПРОСТРАНСТВО';
 const guide=$('teacher-workflow-guide');if(guide)guide.hidden=state.role!=='teacher';if(state.role!=='teacher')return;
 for(const [view,label] of [['lessons','Занятия и заготовки'],['dds','Карточки ДДС'],['scenario-admin','Сценарии 112'],['reports','Результаты и записи'],['cards','Журнал карточек']]){const button=originalMenu.find(b=>b.dataset.view===view);if(button){const icon=button.querySelector('span');button.replaceChildren(icon,document.createTextNode(' '+label));}}
 teacherMenu.replaceChildren();teacherMenuGroup('ПРОВЕДЕНИЕ ЗАНЯТИЙ',['lessons','reports']);teacherMenuGroup('ПОДГОТОВКА ЗАДАНИЙ',['dds','scenario-admin','materials']);teacherMenuGroup('ИСТОРИЯ И ПОМОЩЬ',['cards','issues']);
 for(const button of originalMenu)if(!teacherMenu.contains(button))teacherMenu.append(button);
}
const teacherGuide=el('section','panel teacher-workflow');teacherGuide.id='teacher-workflow-guide';teacherGuide.hidden=true;teacherGuide.append(el('h2','','Проведение занятия'));
for(const [title,text,view] of [['1. Подготовьте задания','Готовые карточки ДДС для проверки и звонков службам.','dds'],['2. Назначьте и запустите занятие','Выберите задания, студентов и условия оценки.','lessons'],['3. Разберите результаты','Исправления, баллы, звонки и записи разговоров.','reports']]){const button=el('button','secondary');button.type='button';button.append(el('b','',title),el('span','',text));button.addEventListener('click',()=>{switchView(view);if(view==='lessons')$('lesson-list').scrollIntoView({behavior:'smooth',block:'start'});});teacherGuide.append(button);}
$('lessons-view').querySelector('.page-heading').after(teacherGuide);
const navigationDomainLoad=domainLoad;domainLoad=async function(){await navigationDomainLoad();configureTeacherNavigation();};
const navigationMonitorLesson=monitorLesson;monitorLesson=async function(identifier){await navigationMonitorLesson(identifier);if(state.role!=='teacher')return;const panel=$('lesson-monitor');const heading=el('div','report-detail-nav');const back=el('button','secondary','← К списку занятий');back.addEventListener('click',()=>{state.monitoredLesson=null;panel.hidden=true;$('lesson-list').scrollIntoView({behavior:'smooth',block:'start'});});heading.append(back,el('p','source-meta','Для прослушивания разговора откройте «Звонки и разбор» у нужной попытки.'));panel.prepend(heading);for(const node of panel.querySelectorAll('.monitor-card')){const match=node.querySelector('b')?.textContent.match(/карточка (\d+)/);if(!match)continue;const button=el('button','primary','Звонки и разбор →');button.addEventListener('click',guarded(async()=>{switchView('reports');await openReportDetail(Number(match[1]));}));node.querySelector('b').after(button);}};

// Open the existing scenario form above its list, preserving all field handlers.
const scenarioEditorForm=$('scenario-create');const scenarioEditorAnchor=document.createComment('scenario-editor-position');scenarioEditorForm.before(scenarioEditorAnchor);
const scenarioEditorDialog=el('dialog','floating-editor scenario-editor-dialog');scenarioEditorDialog.id='scenario-editor-dialog';scenarioEditorDialog.setAttribute('aria-label','Редактирование сценария 112');document.body.append(scenarioEditorDialog);
const scenarioEditorSpacer=el('div');scenarioEditorSpacer.setAttribute('aria-hidden','true');
const scenarioEditorClose=el('button','secondary floating-editor-close','Закрыть редактор');scenarioEditorClose.type='button';scenarioEditorDialog.append(scenarioEditorClose);
let scenarioEditorPagePosition=null;
function closeScenarioEditor(){if(scenarioEditorDialog.open)scenarioEditorDialog.close();if(scenarioEditorForm.parentElement===scenarioEditorDialog){scenarioEditorAnchor.after(scenarioEditorForm);scenarioEditorSpacer.remove();if(scenarioEditorPagePosition)window.scrollTo(scenarioEditorPagePosition.x,scenarioEditorPagePosition.y);scenarioEditorPagePosition=null;}}
scenarioEditorClose.addEventListener('click',closeScenarioEditor);scenarioEditorDialog.addEventListener('cancel',event=>{event.preventDefault();closeScenarioEditor();});$('cancel-edit').addEventListener('click',closeScenarioEditor);
const editBeforeFloating=editScenario;editScenario=function(scenario){const pagePosition={x:window.scrollX,y:window.scrollY};const originalHeight=scenarioEditorForm.getBoundingClientRect().height;editBeforeFloating(scenario);if(scenario.mode==='dds')return;scenarioEditorPagePosition=pagePosition;scenarioEditorSpacer.style.height=(originalHeight||scenarioEditorForm.getBoundingClientRect().height)+'px';scenarioEditorSpacer.style.margin=getComputedStyle(scenarioEditorForm).margin;scenarioEditorAnchor.after(scenarioEditorSpacer);scenarioEditorDialog.append(scenarioEditorForm);if(!scenarioEditorDialog.open)scenarioEditorDialog.showModal();scenarioEditorDialog.scrollTop=0;$('new-title').focus({preventScroll:true});window.scrollTo(pagePosition.x,pagePosition.y);};
const resetBeforeFloating=resetEditor;resetEditor=function(){closeScenarioEditor();resetBeforeFloating();};
const logoutBeforeFloating=logout;logout=function(){if(lessonEditorDialog.open)lessonEditorDialog.close();lessonDraft.open=false;closeScenarioEditor();logoutBeforeFloating();};
