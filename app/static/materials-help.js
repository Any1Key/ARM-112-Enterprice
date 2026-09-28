'use strict';
const trainerGuideSections=[
 ['Начало работы и задания занятия','Преподаватель сначала назначает и запускает занятие. Откройте «Занятия» или «Кабинет ДДС». В списке «Задания занятия» видно, что не начато, находится в работе, пропущено или выполнено. Нажмите «Начать», «Следующая карточка» либо «Продолжить» у пропущенного задания. Если заданий нет, уточните у преподавателя назначение. Выполненное задание доступно через «Результат», повторный запуск в этом занятии заблокирован.'],
 ['Учебный звонок и SMS','Для звонка в браузер нажмите «Подключить телефон», разрешите микрофон и дождитесь регистрации. Выберите задание, нажмите «Учебный звонок» и примите вызов. Используйте одну вкладку телефона на студента. «Завершить разговор» завершает звонок, а «Завершить тренировку» — работу с карточкой. Входящее учебное SMS от преподавателя можно принять из уведомления, если нет другой открытой карточки.'],
 ['Заполнение карточки 112','Запишите место происшествия, сведения о заявителе и телефонах, обстоятельства, количество пострадавших и опасности. Выберите итоговый тип («Класс») через ЕКП, уточните признаки и заполните появившиеся опросные карты. Проверьте автоматически выбранные службы; при необходимости добавьте службу вручную. Не придумывайте сведения, которых заявитель не сообщил.'],
 ['Сохранение, дополнение и завершение','В режиме 112 черновик сохраняется автоматически. «Сохранить карточку» регистрирует её и создаёт список оповещения; после выполнения действия нажмите «Оповещение завершено». Новые сведения вносите через «Дополнить карточку». Закончив, нажмите «Завершить тренировку»; выполненное задание в этом занятии повторно не запускается.'],
 ['Пропуск, возврат и таймер','«Пропустить карточку» сохраняет попытку, а «Продолжить» возвращает к ней. В режиме 112 время работы ставится на паузу. В режиме ДДС часы не останавливаются: время идёт и для пропущенной карточки, и для очереди. Обновление страницы и выход из аккаунта попытку не завершают.'],
 ['Действия ДДС','Вы получаете готовую карточку своей службы: поля 112 и список других получателей можно читать, но нельзя исправлять. За 30 секунд от поступления откройте карточку, за 3 минуты сохраните первый статус с комментарием. После «Принята» последовательно внесите «Начало реагирования», «Прибытие», «Проведение работ», «Работы завершены», каждый с текстом. При «Не принята» нужна причина. Если бригада сообщила об ошибке исходной карточки, уведомите 112 обычным телефоном и зафиксируйте это отдельно. Учебный SIP-звонок руководителю бригады, начальнику или другой службе с номером в карточке записывает доклад. Внешний номер не набирается; автоматических ответов собеседника пока нет.'],
 ['Результат и оценка','В «Результатах» откройте «Разбор» или «Результат» у задания. Посмотрите баллы, замечания, карточку и историю действий. Для ДДС отдельно оцениваются открытие за 30 секунд и первая запись за 3 минуты, оба срока — от поступления в очередь. Содержание звонков и нестандартные решения разбирает преподаватель.'],
 ['Внешний SIP-телефон','Аппарат и компьютер с приложением подключите к одной локальной сети. В «SIP / IP-телефон · функции» нажмите «SIP-телефон»: там пошаговая настройка, имя и короткий пароль вашей аппаратной линии. Сервер на телефоне — IP компьютера с приложением; порт 5060, транспорт UDP. Номер 900 проверяет микрофон и динамик. Для учебного вызова из карточки нажмите «Звонок на аппарат».'],
 ['Журнал, помощь и напоминания','В «Журнале» ищите карточки по номеру, адресу или другим полям. История сохраняет обработку и пропуски. В сохранённой карточке можно установить напоминание или запросить помощь старшего специалиста. Все номера в памятке — учебные внутренние номера тренажёра; реальные экстренные службы не вызываются.']
];
function renderTrainerGuide(){
 const target=$('trainer-guide');target.replaceChildren(el('h2','','Как пользоваться тренажёром'),el('p','','Краткое руководство по работе с занятиями, карточками и звонками. Разверните нужный пункт.'));
 for(const [index,[title,text]] of trainerGuideSections.entries()){const detail=el('details','guide-section');detail.open=index===0;detail.append(el('summary','',`${index+1}. ${title}`),el('p','',text));target.append(detail);}
 if(state.role==='teacher'){const detail=el('details','guide-section');detail.append(el('summary','','Для преподавателя: назначение и разбор'),el('p','','Для 112 создайте или выберите сценарий, проверьте эталон, утвердите его и назначьте в «Занятиях» студентам. Для ДДС в «Кабинете ДДС» подготовьте карточки из системных сценариев или завершённых карточек своих студентов, проверьте получателей, выберите одну службу и назначьте занятие. Нажмите «Начать занятие»: до этого студенты не получат карточки. В мониторинге видны текущие действия; в «Результатах» — ответы, статусы, сроки, пропуски и записи звонков. Добавьте экспертную оценку и комментарий. Для повторной практики назначьте сценарий в новом занятии.'));target.append(detail);}
}
async function renderServiceNumberMemo(){
 try{
  const items=await api('/api/services/directory');
  const detail=el('details','guide-section');detail.append(el('summary','','Учебные номера служб'));
  const list=el('ul','service-number-memo');
  for(const item of items)list.append(el('li','',`${item.extension} — ${item.name}`));
  detail.append(list);$('trainer-guide').append(detail);
 }catch(error){/* The static guide remains available if the directory is unavailable. */}
}
const renderMaterialsBeforeHelp=renderMaterials;
renderMaterials=async function(){renderTrainerGuide();const student=state.role==='student';show('classifier-summary',!student);show('ticket-controls',!student);await renderMaterialsBeforeHelp();await renderServiceNumberMemo();};
let manualDocument=null,manualPending=null;
async function getManualDocument(){
 const token=state.token;
 if(manualDocument?.token===token)return manualDocument;
 if(manualPending?.token===token)return manualPending.promise;
 const pending={token};pending.promise=(async()=>{
  const response=await fetch('/api/materials/source/manual',{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(15000)});
  if(!response.ok)throw Error('Не удалось загрузить PDF-памятку. Повторите попытку.');
  const blob=await response.blob();if(state.token!==token)throw Error('Войдите в систему, чтобы открыть памятку.');
  if(manualDocument)URL.revokeObjectURL(manualDocument.url);
  manualDocument={token,url:URL.createObjectURL(blob),filename:'Памятка_АРМ-112_для_ДДС.pdf'};return manualDocument;
 })().finally(()=>{if(manualPending===pending)manualPending=null;});manualPending=pending;return pending.promise;
}
async function showManual(page=1){
 const status=$('manual-status');status.textContent='Загрузка PDF-памятки…';status.hidden=false;
 try{const document=await getManualDocument();$('manual-frame').src=document.url+'#page='+page;$('manual-new-tab').href=document.url+'#page='+page;show('manual-reader',true);status.textContent='Документ готов к чтению.';$('manual-reader').scrollIntoView({block:'start'});}catch(error){status.textContent=error.name==='TimeoutError'?'Загрузка заняла слишком много времени. Нажмите «Читать PDF» ещё раз.':error.message;throw error;}
}
const openSourceBeforeManualReader=openSource;
openSource=async function(kind,page=1){if(kind==='manual')return showManual(page);return openSourceBeforeManualReader(kind,page);};
$('read-manual').addEventListener('click',guarded(()=>showManual()));
$('download-manual').addEventListener('click',guarded(async()=>{const document=await getManualDocument();const anchor=el('a');anchor.href=document.url;anchor.download=document.filename;anchor.click();}));
$('close-manual').addEventListener('click',()=>show('manual-reader',false));
const logoutBeforeManualReader=logout;
logout=function(){if(manualDocument)URL.revokeObjectURL(manualDocument.url);manualDocument=null;manualPending=null;$('manual-frame').removeAttribute('src');$('manual-new-tab').removeAttribute('href');show('manual-reader',false);show('manual-status',false);logoutBeforeManualReader();};


const ticketCatalogNav=el('button','nav-item');ticketCatalogNav.id='ticket-catalog-nav';ticketCatalogNav.hidden=true;ticketCatalogNav.append(el('span','','▤'),document.createTextNode('Билеты и задачи'));ticketCatalogNav.addEventListener('click',guarded(async()=>{switchView('materials');await renderMaterials();$('ticket-controls').scrollIntoView({behavior:'smooth',block:'start'});}));document.querySelector('.sidebar nav').append(ticketCatalogNav);
$('ticket-controls').prepend(el('h2','','Билеты и задачи из PDF'));
const ticketAccessDomain=domainLoad;domainLoad=async function(){await ticketAccessDomain();ticketCatalogNav.hidden=state.role==='student';if(state.role==='student'){state.materials=null;$('ticket-list').replaceChildren();if(sourceUrl){URL.revokeObjectURL(sourceUrl);sourceUrl=null;}}};
