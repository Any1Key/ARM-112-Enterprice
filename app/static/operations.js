'use strict';
const opsNav=el('button','nav-item');opsNav.id='operations-nav';opsNav.hidden=true;opsNav.append(el('span','','◉'),document.createTextNode('Мониторинг'));opsNav.addEventListener('click',()=>{switchView('operations');loadOperations().catch(error=>notify(error.message));});document.querySelector('.sidebar nav').append(opsNav);
const opsSection=el('section');opsSection.id='operations-view';opsSection.hidden=true;opsSection.append(el('h1','','Мониторинг учебного контура'),el('p','source-meta','Состояние компонентов и резервной копии · обновление каждые 5 секунд'));const opsTable=el('div','panel table-panel');opsSection.append(opsTable);document.querySelector('main').append(opsSection);
const backupButton=el('button','button','Создать резервную копию');
const backupInfo=el('p','source-meta');opsSection.append(backupButton,backupInfo);
backupButton.addEventListener('click',async()=>{backupButton.disabled=true;try{await api('/api/operations/backup',{method:'POST'});notify('Резервная копия поставлена в очередь');await loadOperations();}catch(error){notify(error.message);backupButton.disabled=false;}});
async function loadOperations(){const data=await api('/api/operations');const names={postgresql:'PostgreSQL',redis:'Redis',asterisk:'Asterisk',voice:'Русский голос',ml:'Нейросетевая оценка',grammar:'Грамматика',ollama:'Генерация сценариев',backup:'Резервная копия'};table(opsTable,['Компонент','Состояние','Детали'],Object.entries(data.components).map(([name,value])=>[names[name],value.status==='ok'?'Работает':value.status==='stale'?'Копия устарела':value.status==='missing'?'Копия отсутствует':value.status==='incomplete'?'Копия неполная':'Недоступен',name==='backup'?`Возраст: ${value.age_seconds??'—'} с · размер: ${value.size_bytes==null?'—':(value.size_bytes/1048576).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2})+' МБ'}`:value.reason||'']));const backup=data.components.backup;backupButton.disabled=backup.running||backup.queued||!backup.scheduler_alive;backupInfo.textContent=`Автоматически каждые ${backup.interval_hours} ч · база данных, аудио и вложения · ${backup.running?'Создаётся копия':backup.queued?'Ожидает запуска':!backup.scheduler_alive?'Сервис резервирования недоступен':backup.last_failed?'Последняя попытка не удалась; сервис повторит запуск':'Готово к запуску'}${backup.restore_check.verified_at?' · Восстановление проверено '+new Date(backup.restore_check.verified_at).toLocaleString('ru-RU')+(backup.restore_check.bundle===backup.bundle?' (текущая копия)':' (предыдущая копия)'):''}`;opsTable.append(el('p','source-meta',`Активных сессий: ${data.active_runs} · пользователей: ${data.users} · событий аудита: ${data.audit_events} · RAM приложения: ${data.process_ram_mb==null?'—':data.process_ram_mb.toLocaleString('ru-RU')+' МБ'} · CPU приложения: ${data.process_cpu_percent==null?'измеряется…':data.process_cpu_percent.toLocaleString('ru-RU')+' %'}`));}
opsSection.append(el('p','source-meta','RAM — текущая оперативная память приложения. CPU — нагрузка приложения за последнюю секунду; 100% соответствует одному ядру. Показатели других контейнеров сюда не входят.'));
setInterval(()=>{opsNav.hidden=state.role!=='admin';if(state.token&&state.role==='admin'&&!opsSection.hidden)loadOperations().catch(()=>{});},5000);

const backupsDetails=el('details','panel');backupsDetails.id='backup-history';
const backupsSummary=el('summary','','Резервные копии');
const backupsList=el('div','table-panel');
backupsDetails.append(backupsSummary,el('p','source-meta','Даты показаны по времени вашего компьютера. «Скачать всё» сохраняет пользователей, карточки, историю, аудио и вложения одним архивом. «Скачать БД» сохраняет только базу.'),backupsList);opsSection.append(backupsDetails);
let backupsLoading=false;
const backupMegabytes=value=>value==null?'—':(value/1048576).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2})+' МБ';
async function loadBackupHistory(){
 if(backupsLoading)return;backupsLoading=true;
 try{const items=await api('/api/operations/backups');backupsSummary.textContent=`Резервные копии (${items.length})`;
 table(backupsList,['Дата и время','Состав','Размер копии','База данных','Состояние','Действия'],items.map(item=>{
 const button=el('button','button','Скачать БД');button.disabled=!item.complete;
 button.addEventListener('click',async()=>{button.disabled=true;try{
 const response=await fetch(`/api/operations/backups/${encodeURIComponent(item.id)}/database`,{headers:{Authorization:'Bearer '+state.token}});
 if(!response.ok)throw new Error('Не удалось скачать базу. Обновите список и повторите попытку.');
 const blob=await response.blob();const url=URL.createObjectURL(blob);const anchor=el('a');anchor.href=url;anchor.download=item.id.endsWith('.dump')?item.id:item.id+'.dump';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
 }catch(error){notify(error.message);}finally{button.disabled=!item.complete;}});
 const actions=el('div','quick-actions');const restore=el('button','button','Восстановить БД');restore.disabled=!item.complete;restore.addEventListener('click',()=>openDatabaseRestore({backup_id:item.id,label:new Date(item.created_at).toLocaleString('ru-RU')+' · '+item.id}));actions.append(button,restore);if(item.scope.includes('media')){const all=el('button','button','Скачать всё');all.addEventListener('click',async()=>{all.disabled=true;all.textContent='Подготовка…';try{const result=await api(`/api/operations/backups/${encodeURIComponent(item.id)}/export`,{method:'POST'});const anchor=el('a');anchor.href=result.url;anchor.download=result.filename;document.body.append(anchor);anchor.click();anchor.remove();}catch(error){notify(error.message);}finally{all.disabled=false;all.textContent='Скачать всё';}});const fullRestore=el('button','button','Восстановить всё');fullRestore.disabled=!item.complete;fullRestore.addEventListener('click',()=>openDatabaseRestore({backup_id:item.id,full:true,label:new Date(item.created_at).toLocaleString('ru-RU')+' · '+item.id}));all.disabled=!item.complete;actions.prepend(all,fullRestore);}
 return [new Date(item.created_at).toLocaleString('ru-RU'),item.scope.includes('media')?'БД, аудио и вложения':'Только БД',backupMegabytes(item.size_bytes),backupMegabytes(item.database_size_bytes),item.complete?'Готова':'Неполная',actions];}));
 }finally{backupsLoading=false;}
}
backupsDetails.addEventListener('toggle',()=>{if(backupsDetails.open)loadBackupHistory().catch(error=>notify(error.message));});
setInterval(()=>{if(state.token&&state.role==='admin'&&!opsSection.hidden&&backupsDetails.open)loadBackupHistory().catch(()=>{});},5000);

const restorePanels=el('div','backup-panels');opsSection.append(restorePanels);restorePanels.append(backupsDetails);
const uploadDetails=el('details','panel');uploadDetails.id='database-upload';uploadDetails.append(el('summary','','Загрузить копию'));
const uploadFile=el('input');uploadFile.type='file';uploadFile.accept='.tar.gz,.dump,.backup';uploadFile.id='restore-database-file';
const uploadButton=el('button','button','Загрузить и восстановить');uploadButton.id='restore-upload-button';uploadButton.disabled=true;
const uploadName=el('p','source-meta','Выберите полную копию .tar.gz (до 2 ГБ) или отдельную БД .dump (до 100 МБ).');
uploadFile.addEventListener('change',()=>{uploadButton.disabled=!uploadFile.files.length;uploadName.textContent=uploadFile.files[0]?`${uploadFile.files[0].name} · ${backupMegabytes(uploadFile.files[0].size)}`:'Файл не выбран';});
uploadDetails.append(uploadName,uploadFile,el('p','source-meta','Полная копия восстанавливает пользователей, карточки, историю, аудио и вложения. Отдельный .dump восстанавливает только БД. Версии приложения на обоих серверах должны совпадать; IP и настройки нового сервера задаются в его .env.'),uploadButton);restorePanels.append(uploadDetails);
uploadButton.addEventListener('click',()=>{const file=uploadFile.files[0];if(file)openDatabaseRestore({file,full:file.name.toLowerCase().endsWith('.tar.gz'),label:file.name});});

const restoreDialog=el('dialog','issue-dialog restore-dialog');restoreDialog.id='database-restore-dialog';document.body.append(restoreDialog);
let restorePolling=false;
function openDatabaseRestore(source){
 restoreDialog.replaceChildren(el('h2','',source.full?'Восстановить полную копию':'Восстановить базу данных'));
 restoreDialog.append(el('p','',`Источник: ${source.label}`),el('p','restore-warning','Рабочая база будет заменена: более новые карточки, результаты и пользователи исчезнут. Перед заменой сохранится полная резервная копия текущих данных. Во время восстановления работа системы будет заблокирована.'),el('p','source-meta',(source.full?'Аудио и вложения также будут заменены данными из полной копии. ':'Аудио и вложения остаются на этом сервере. ' )+'После восстановления все пользователи должны войти заново с учётными записями из выбранной базы и переподключить SIP-телефоны.'));
 const form=el('form');const password=el('input');password.type='password';password.required=true;password.autocomplete='current-password';password.id='restore-admin-password';
 const confirmation=el('input');confirmation.required=true;confirmation.id='restore-confirmation';confirmation.placeholder='ВОССТАНОВИТЬ';
 const trust=el('input');trust.type='checkbox';trust.required=true;trust.id='restore-trusted-source';
 for(const [text,input] of [['Ваш текущий пароль администратора',password],['Введите ВОССТАНОВИТЬ для подтверждения',confirmation]]){const label=el('label','',text);label.append(input);form.append(label);}
 const trustLabel=el('label','restore-trust');trustLabel.append(trust,document.createTextNode('Копия получена с доверенного сервера этой системы'));form.append(trustLabel);
 const error=el('p','source-meta');error.setAttribute('role','alert');const submit=el('button','primary',source.full?'Восстановить всё':'Восстановить БД');submit.type='submit';const cancel=el('button','secondary','Отмена');cancel.type='button';cancel.addEventListener('click',()=>restoreDialog.close());const buttons=el('div','quick-actions');buttons.append(submit,cancel);form.append(error,buttons);restoreDialog.append(form);
 form.addEventListener('submit',async event=>{event.preventDefault();if(confirmation.value!=='ВОССТАНОВИТЬ'){error.textContent='Введите ВОССТАНОВИТЬ';return;}submit.disabled=true;cancel.disabled=true;try{
 let uploadId;
 if(source.file){if(source.file.size>(source.full?2147483648:104857600))throw Error('Размер файла превышает '+(source.full?'2 ГБ':'100 МБ'));error.textContent=source.full?'Загружается полная копия…':'Загружается база данных…';const response=await fetch('/api/operations/restore/uploads'+(source.full?'?kind=full':''),{method:'POST',headers:{Authorization:'Bearer '+state.token,'Content-Type':'application/octet-stream'},body:source.file});const data=await response.json();if(!response.ok)throw Error(data.detail||'Не удалось загрузить копию');uploadId=data.upload_id;}
 const job=await api('/api/operations/restore/jobs',{method:'POST',body:JSON.stringify({scope:source.full?'full':'database',backup_id:source.backup_id||null,upload_id:uploadId||null,password:password.value,confirmation:confirmation.value,trusted_source:trust.checked})});password.value='';
 sessionStorage.setItem('database-restore-job',JSON.stringify(job));pollDatabaseRestore(job);
 }catch(exc){error.textContent=exc.message;submit.disabled=false;cancel.disabled=false;}});
 restoreDialog.showModal();
}
async function pollDatabaseRestore(job){
 if(restorePolling)return;restorePolling=true;restoreDialog.replaceChildren(el('h2','','Восстановление копии'));const progress=el('p','');progress.setAttribute('role','status');progress.id='restore-progress';restoreDialog.append(progress);if(!restoreDialog.open)restoreDialog.showModal();
 const preventClose=event=>event.preventDefault();restoreDialog.addEventListener('cancel',preventClose);
 try{while(true){
  try{const response=await fetch(`/api/operations/restore/jobs/${job.job_id}`,{headers:{'X-Restore-Token':job.status_token}});if(!response.ok)throw Error('Статус временно недоступен');const status=await response.json();progress.textContent=status.message;
   if(status.status==='succeeded'||status.status==='failed'){
    sessionStorage.removeItem('database-restore-job');
    if(status.safety_backup)restoreDialog.append(el('p','source-meta',`Копия перед восстановлением: ${status.safety_backup}`));
    if(status.warning)restoreDialog.append(el('p','source-meta',status.warning));
    if(status.status==='succeeded'){logout();uploadFile.value='';uploadButton.disabled=true;}
    const close=el('button','primary',status.status==='succeeded'?'Войти заново':'Закрыть');close.addEventListener('click',()=>{restoreDialog.close();if(status.status==='failed')loadOperations().catch(()=>{});});restoreDialog.append(close);break;
   }
  }catch{progress.textContent='Ожидаем ответ сервиса восстановления. Проверка продолжится автоматически; страницу можно обновить.';}
  await new Promise(resolve=>setTimeout(resolve,1500));
 }}finally{restorePolling=false;restoreDialog.removeEventListener('cancel',preventClose);}
}
try{const saved=sessionStorage.getItem('database-restore-job');if(saved)pollDatabaseRestore(JSON.parse(saved));}catch{sessionStorage.removeItem('database-restore-job');}
