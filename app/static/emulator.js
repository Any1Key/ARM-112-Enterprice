'use strict';
// Reuse API-backed controls, place them in the order shown on manual page 15.
const form=$('incident-form'),columns=form.querySelector('.form-columns');
const callerFields=$('caller-name').closest('.field-pair');callerFields.classList.add('caller-fields');columns.firstElementChild.prepend(callerFields);
const flagTop=el('div','flag-top');for(const id of ['victims','access-blocked','no-contact','call-lost']){const label=$(id).closest('label');flagTop.append(label);}columns.lastElementChild.prepend(flagTop);
const timerStrip=form.querySelector('.call-strip');form.querySelector('.telephony-strip').append(timerStrip);
const toggle=el('button','secondary','Сценарии занятия ▾');toggle.type='button';toggle.addEventListener('click',()=>document.querySelector('.scenario-panel').classList.toggle('collapsed'));document.querySelector('#training-view > .page-heading').append(toggle);
const labels={training:'Карточка',cards:'Журнал',materials:'Памятка',lessons:'Занятия',reports:'Отчёты','scenario-admin':'Сценарии',users:'Пользователи',audit:'Аудит'};
for(const button of document.querySelectorAll('[data-view]')){const icon=button.querySelector('span');if(icon){button.replaceChildren(icon,document.createTextNode(labels[button.dataset.view]));}}
const heading=el('div','source-grid-head');for(const value of ['','Важн.','ЧС','Опер.','АРМ','Номер','Дата','Время','Тип происшествия','Повт.','Статус','Адрес','Проверена'])heading.append(el('span','',value));$('card-feed').before(heading);
const footer=el('div','journal-footer','Учебный журнал · до 200 последних доступных карточек');$('card-feed').after(footer);

const originalDomainLoad=domainLoad;domainLoad=async()=>{await originalDomainLoad();const away=document.querySelector('#incident-form [data-flag="victims_away"]');if(away&&!flagTop.contains(away))flagTop.insertBefore(away.closest("label"),flagTop.children[1]);};
const addressFields=form.querySelector('.address-fields');
function addressRow(names,kind){const row=el('div','address-row '+kind);for(const name of names)row.append($(name).closest('label'));return row;}
const addressRows=[addressRow(['country','region','city'],'three'),addressRow(['object-name','borough','district'],'three'),addressRow(['street','house','building'],'street'),addressRow(['structure','apartment','entrance','floor','access-code'],'five')];
const descriptive=$('descriptive-address').closest('label');addressFields.replaceChildren(...addressRows,descriptive);
const summary=$('address').closest('label');summary.classList.add('address-summary');addressFields.before(summary);
const newCard=el('button','journal-new-card','Создать новую карточку');newCard.type='button';newCard.addEventListener('click',()=>{switchView('training');document.querySelector('.scenario-panel').classList.remove('collapsed');});document.querySelector('.topbar').append(newCard);
const originalRestoreRun=restoreRun;restoreRun=async run=>{switchView('training');await originalRestoreRun(run);document.querySelector('.scenario-panel').classList.add('collapsed');};
const arrangeSourceFlags=()=>{for(const [id,text] of [['victims','Пострадавшие'],['access-blocked','Нет доступа / Заблокированные'],['no-contact','нет контакта'],['call-lost','срыв звонка']]){const input=$(id);input.closest('label').replaceChildren(input,document.createTextNode(text));}};arrangeSourceFlags();

const previousPage=el('button','secondary','‹'),nextPage=el('button','secondary','›'),pageInfo=el('span');footer.replaceChildren(pageInfo,previousPage,nextPage);previousPage.addEventListener('click',guarded(async()=>{state.cardOffset=Math.max(0,(state.cardOffset||0)-10);await loadFeed();}));nextPage.addEventListener('click',guarded(async()=>{state.cardOffset=(state.cardOffset||0)+10;await loadFeed();}));document.addEventListener('arm:feed',()=>{pageInfo.textContent=`Страница ${Math.floor((state.cardOffset||0)/10)+1} · записей на странице: 10 · показано: ${state.feedPageCount} `;previousPage.disabled=!state.cardOffset;nextPage.disabled=state.feedPageCount<10;});
