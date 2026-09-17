'use strict';
const phone=el('div','sip-controls');phone.hidden=true;const connectPhone=el('button','','Подключить телефон');connectPhone.type='button';const phoneStatus=el('span','','Не подключён');phoneStatus.id='sip-status';const startPhone=el('button','','Учебный звонок');startPhone.type='button';const hangupPhone=el('button','','Завершить разговор');hangupPhone.type='button';phone.append(connectPhone,phoneStatus,startPhone,hangupPhone);document.querySelector('.topbar').prepend(phone);
const remoteAudio=el('audio');remoteAudio.autoplay=true;remoteAudio.controls=false;remoteAudio.preload='metadata';remoteAudio.hidden=true;remoteAudio.setAttribute('aria-label','Аудио учебного звонка');phone.append(remoteAudio);
const disconnectPhone=el('button','','Отключить телефон');disconnectPhone.type='button';disconnectPhone.disabled=true;phone.insertBefore(disconnectPhone,phoneStatus);disconnectPhone.addEventListener('click',()=>disconnectTrainingPhone());
let sipUA=null,sipSession=null,sipRun=null,sipIceServers=[];
let sipCallId=null,sipAttempt=0,sipStarting=false,sipPollBusy=false;
let releasePhoneLock=null,lockedPhoneUser=null;
async function lockBrowserPhone(username){
  if(lockedPhoneUser===username||!navigator.locks?.request)return;
  if(releasePhoneLock)releasePhoneLock();
  const acquired=await new Promise((resolve,reject)=>{
    navigator.locks.request('arm112-sip-'+username,{ifAvailable:true},lock=>{
      if(!lock){resolve(false);return;}
      lockedPhoneUser=username;
      return new Promise(unlock=>{releasePhoneLock=()=>{releasePhoneLock=null;lockedPhoneUser=null;unlock();};resolve(true);});
    }).catch(reject);
  });
  if(!acquired)throw Error('Телефон этого студента уже подключён в другой вкладке. Отключите его там перед подключением здесь.');
}
const preparedAudio=new Map();
function warmSelectedAudio(){
  const scenario=state.selected;
  if(!state.token||state.role!=='student'||!sipUA?.isRegistered()||!scenario||scenario.mode==='dispatch')return;
  const key=scenario.id+':'+scenario.caller_text;
  if(preparedAudio.has(key))return;
  const pending=api(`/api/telephony/scenarios/${scenario.id}/prepare`,{method:'POST'}).catch(()=>preparedAudio.delete(key));
  preparedAudio.set(key,pending);if(preparedAudio.size>100)preparedAudio.delete(preparedAudio.keys().next().value);
}
const chooseBeforeAudio=choose;
choose=function(scenario){chooseBeforeAudio(scenario);if(state.selected===scenario)warmSelectedAudio();};
async function connectSip(){
  if(!state.token||state.role!=='student')throw Error('Телефон подключает обучающийся');
  if(!window.isSecureContext)throw Error('Для микрофона откройте localhost или учебный контур по HTTPS');
  if(!window.JsSIP)throw Error('SIP-клиент не загружен');
  if(sipSession&&!sipSession.isEnded())throw Error('Завершите разговор перед переподключением телефона');
  await navigator.mediaDevices.getUserMedia({audio:true}).then(stream=>stream.getTracks().forEach(track=>track.stop()));
  const config=await api('/api/telephony/account',{method:'POST'});await lockBrowserPhone(config.username);sipIceServers=config.ice_servers||[];
  const websocket=location.protocol==='https:'?`wss://${location.host}${config.ws_path}`:`ws://${location.hostname}:${config.ws_port}/ws`;
  if(sipUA)sipUA.stop();
  const ua=new JsSIP.UA({sockets:[new JsSIP.WebSocketInterface(websocket)],uri:`sip:${config.username}@${config.domain}`,password:config.password,register:true,session_timers:false});sipUA=ua;disconnectPhone.disabled=false;
  ua.on('registered',()=>{if(sipUA!==ua)return;phoneStatus.textContent='Телефон подключён';connectPhone.disabled=true;warmSelectedAudio();});
  ua.on('registrationFailed',()=>{if(sipUA!==ua)return;phoneStatus.textContent='Ошибка регистрации';connectPhone.disabled=false;});
  ua.on('disconnected',()=>{if(sipUA!==ua)return;phoneStatus.textContent='Нет соединения';connectPhone.disabled=false;});
  ua.on('newRTCSession',event=>{
    if(event.originator!=='remote')return;
    const session=event.session;
    if(sipUA!==ua||!state.runId||sipRun!==state.runId||(sipSession&&!sipSession.isEnded())){session.terminate();return;}
    sipSession=session;phoneStatus.textContent='Входящий учебный вызов';startPhone.disabled=true;hangupPhone.textContent='Завершить разговор';
    const audio=connection=>connection.addEventListener('track',event=>{if(sipSession!==session)return;remoteAudio.srcObject=event.streams[0];remoteAudio.play().catch(()=>notify('Нажмите принять вызов для воспроизведения звука.'));});
    session.on('peerconnection',event=>audio(event.peerconnection));
    session.on('accepted',()=>{if(sipSession===session)phoneStatus.textContent='Разговор';});
    const ended=()=>{if(sipSession!==session)return;sipSession=null;startPhone.disabled=false;phoneStatus.textContent='Разговор завершён';};
    session.on('ended',ended);session.on('failed',event=>{if(sipSession!==session)return;ended();notify('Звонок не состоялся: '+event.cause);connectPhone.disabled=false;});
    if(session.connection)audio(session.connection);show('call',true);show('incident-form',false);$('accept-call').disabled=false;
  });ua.start();
}
connectPhone.addEventListener('click',guarded(()=>connectSip()));
async function startTrainingCall(){
  if(sipStarting||(sipSession&&!sipSession.isEnded()))throw Error('Звонок уже готовится или идёт');
  if(!sipUA?.isRegistered())throw Error('Подключите телефон');
  if(!state.selected)throw Error('Выберите сценарий занятия');
  if(state.selected.mode==='dispatch')throw Error('В ДДС используется входящая карточка');
  const attempt=++sipAttempt;sipStarting=true;sipCallId=null;startPhone.disabled=true;phoneStatus.textContent='Подготовка вызова';
  try{
    const run=state.runId?await api(`/api/runs/${state.runId}`):await api(`/api/runs/${state.selected.id}/start`,{method:'POST'});
    if(sipAttempt!==attempt)return;
    await restoreRun(run);if(sipAttempt!==attempt)return;
    sipRun=run.run_id;state.channel='SIP / IP-телефон';if($('call-channel'))$('call-channel').value=state.channel;show('call',true);show('incident-form',false);$('accept-call').disabled=true;
    $('call-title').textContent=state.selected.title;$('call-help').textContent='Готовим озвучку и соединяем учебный SIP-вызов';
    document.body.classList.add('phone-mode');hangupPhone.textContent='Отменить звонок';
    const result=await api(`/api/telephony/runs/${sipRun}/call`,{method:'POST'});
    if(sipAttempt!==attempt){await api(`/api/telephony/runs/${run.run_id}/call/cancel`,{method:'POST'});return;}
    sipCallId=result.call_id;
  }catch(error){if(sipAttempt===attempt){sipRun=null;sipCallId=null;phoneStatus.textContent='Не удалось начать звонок';connectPhone.disabled=false;}throw error;}
  finally{if(sipAttempt===attempt){sipStarting=false;startPhone.disabled=Boolean(sipRun);}}
}
startPhone.addEventListener('click',guarded(startTrainingCall));
function answerSip(){if(sipSession){sipSession.answer({mediaConstraints:{audio:true,video:false},pcConfig:{iceServers:sipIceServers}});remoteAudio.play().catch(()=>{});}}
async function cancelTrainingCall(){
  const run=sipRun;++sipAttempt;sipStarting=false;sipRun=null;sipCallId=null;startPhone.disabled=false;
  if(sipSession&&!sipSession.isEnded())sipSession.terminate();sipSession=null;
  phoneStatus.textContent='Звонок завершён';hangupPhone.textContent='Завершить разговор';
  if(run)await api(`/api/telephony/runs/${run}/call/cancel`,{method:'POST'});
}
hangupPhone.addEventListener('click',guarded(cancelTrainingCall));
async function pollTrainingCall(){
  if(!state.token||state.role!=='student'){if(sipUA)disconnectTrainingPhone();return;}
  if(!sipRun||sipPollBusy)return;
  const run=sipRun,attempt=sipAttempt,call=sipCallId;sipPollBusy=true;
  try{
    const calls=await api(`/api/telephony/runs/${run}`,{signal:AbortSignal.timeout(5000)});
    // A response from an earlier attempt must never modify the new call.
    if(run!==sipRun||attempt!==sipAttempt||call!==sipCallId)return;
    const latest=call?calls.find(x=>x.id===call):sipStarting?null:calls.at(-1);if(!latest)return;
    if(latest.state==='queued'){const seconds=Math.max(0,Math.floor((Date.now()-new Date(latest.created_at))/1000));phoneStatus.textContent=`Готовим озвучку · ${seconds} сек.`;}
    if(latest.state==='ringing'&&!sipSession)phoneStatus.textContent='Соединяем с телефоном';
    if(['failed','ended','cancelled'].includes(latest.state)){
      sipRun=null;sipCallId=null;startPhone.disabled=false;
      if(latest.state==='failed'){phoneStatus.textContent='Вызов не состоялся';connectPhone.disabled=false;notify(latest.error);}
    }
  }catch{/* The next poll retries after a short network interruption. */}
  finally{sipPollBusy=false;}
}
setInterval(pollTrainingCall,1000);

const recordingButton=el('button','','Запись звонка');recordingButton.type='button';const recordingPanel=el('div','recording-player');recordingPanel.hidden=true;const playRecording=el('button','','▶');playRecording.type='button';playRecording.setAttribute('aria-label','Пауза');const seekRecording=document.createElement('input');seekRecording.type='range';seekRecording.min='0';seekRecording.max='0';seekRecording.step='0.1';seekRecording.value='0';seekRecording.setAttribute('aria-label','Позиция записи');const recordingTime=el('span','','00:00 / 00:00');recordingPanel.append(playRecording,seekRecording,recordingTime);phone.append(recordingButton,recordingPanel);
let recordingUrl=null;
function formatAudioTime(value){if(!Number.isFinite(value))return '00:00';return `${String(Math.floor(value/60)).padStart(2,'0')}:${String(Math.floor(value%60)).padStart(2,'0')}`;}
function syncRecordingProgress(){seekRecording.value=String(remoteAudio.currentTime||0);recordingTime.textContent=`${formatAudioTime(remoteAudio.currentTime)} / ${formatAudioTime(remoteAudio.duration)}`;playRecording.textContent=remoteAudio.paused?'▶':'Ⅱ';playRecording.setAttribute('aria-label',remoteAudio.paused?'Воспроизвести запись':'Поставить на паузу');}
remoteAudio.addEventListener('loadedmetadata',()=>{seekRecording.max=String(remoteAudio.duration||0);syncRecordingProgress();});remoteAudio.addEventListener('timeupdate',syncRecordingProgress);remoteAudio.addEventListener('ended',syncRecordingProgress);
seekRecording.addEventListener('input',()=>{remoteAudio.currentTime=Number(seekRecording.value);syncRecordingProgress();});playRecording.addEventListener('click',guarded(async()=>{if(!recordingUrl)throw Error('Сначала загрузите запись');if(remoteAudio.paused)await remoteAudio.play();else remoteAudio.pause();syncRecordingProgress();}));
recordingButton.addEventListener('click',guarded(async()=>{const identifier=state.runId||state.reports.find(run=>run.finished_at)?.id;if(!identifier)throw Error('Нет учебного звонка');const response=await fetch(`/api/telephony/runs/${identifier}/recording`,{headers:{Authorization:'Bearer '+state.token}});if(!response.ok)throw Error('Запись пока недоступна');if(recordingUrl)URL.revokeObjectURL(recordingUrl);recordingUrl=URL.createObjectURL(await response.blob());remoteAudio.srcObject=null;remoteAudio.src=recordingUrl;remoteAudio.hidden=true;remoteAudio.load();recordingPanel.hidden=false;await remoteAudio.play();syncRecordingProgress();}));

function stopTrainingPhone(){cancelTrainingCall().catch(()=>{});++sipAttempt;sipStarting=false;sipCallId=null;startPhone.disabled=false;sipRun=null;if(sipSession)sipSession.terminate();sipSession=null;remoteAudio.pause();remoteAudio.srcObject=null;remoteAudio.removeAttribute('src');recordingPanel.hidden=true;document.body.classList.remove('phone-mode');phoneStatus.textContent=sipUA?.isRegistered()?'Телефон подключён':'Не подключён';}

function disconnectTrainingPhone(){
  cancelTrainingCall().catch(()=>{});
  if(sipUA){sipUA.stop();sipUA=null;}
  if(releasePhoneLock)releasePhoneLock();
  preparedAudio.clear();connectPhone.disabled=false;disconnectPhone.disabled=true;phoneStatus.textContent='Телефон отключён';document.body.classList.remove('phone-mode');
}
