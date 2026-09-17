const {chromium}=require('playwright');
const assert=require('node:assert/strict');const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream','--autoplay-policy=no-user-gesture-required','--disable-background-timer-throttling']});
 const adminContext=await browser.newContext();const admin=await adminContext.newPage();let user;let student;
 const errors=[];let identifier;
 async function login(page,name,password){await page.goto(process.env.BASE_URL||'http://localhost:8000');page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());await page.locator('#user').fill(name);await page.locator('#pass').fill(password);await page.locator('#login-submit').click();await page.locator('#workspace').waitFor({state:'visible'});await page.waitForFunction(()=>!$('login-submit').disabled);}
 try{
  await login(admin,'admin',process.env.ADMIN_PASSWORD||'admin12345');
  const name='call_report_proof_'+Date.now();user=await admin.evaluate(name=>api('/api/users',{method:'POST',body:JSON.stringify({username:name,password:'Proof-password-12345',role:'student'})}),name);
  const context=await browser.newContext({permissions:['microphone']});student=await context.newPage();await login(student,name,'Proof-password-12345');
  // Deliberately return the previous call status after a new attempt is current.
  await student.evaluate(async()=>{const original=api;let resolve;sipRun=100;sipCallId=500;sipAttempt=10;api=()=>new Promise(r=>resolve=r);const pending=pollTrainingCall();sipAttempt=11;sipCallId=501;resolve([{id:500,state:'ended'}]);await pending;if(sipRun!==100||sipCallId!==501)throw Error('Old polling cleared the current call');api=original;sipRun=null;sipCallId=null;});console.log('Stale polling race: same run / different call does not cancel new attempt PASS');
  await student.evaluate(()=>{choose(state.scenarios.find(s=>s.title.includes('SMS · человек')));return connectSip();});await student.waitForFunction(()=>sipUA?.isRegistered(),{},{timeout:30000});
  const attempts=[];
  for(let i=0;i<Number(process.env.CALLS||5);i++){
   const start=Date.now();await student.evaluate(()=>startTrainingCall());identifier=await student.evaluate(()=>state.runId);
   await student.waitForFunction(()=>sipSession&&!$('accept-call').disabled,{},{timeout:25000});await student.locator('#accept-call').click();await student.waitForFunction(()=>!state.busy&&sipSession?.isEstablished(),{},{timeout:25000});
   let audio;for(let n=0;n<60;n++){audio=await student.evaluate(async()=>{if(!sipSession?.connection)return false;return [...(await sipSession.connection.getStats()).values()].some(x=>x.type==='inbound-rtp'&&x.kind==='audio'&&x.bytesReceived>0);});if(audio)break;await new Promise(r=>setTimeout(r,100));}assert(audio,'Inbound RTP');
   const calls=await student.evaluate(()=>api(`/api/telephony/runs/${state.runId}`));const call=calls.at(-1);attempts.push({id:call.id,to_audio_ms:Date.now()-start,voice:call.audio?.voice,cached:call.audio?.cached,preparation_ms:call.audio?.preparation_ms});
   await student.evaluate(()=>cancelTrainingCall());await student.waitForFunction(()=>!sipSession);await new Promise(r=>setTimeout(r,200));
  }
  assert.equal(new Set(attempts.map(a=>a.voice)).size,1);assert(attempts.slice(1).every(a=>a.cached));console.log('Consecutive real calls, same card, RTP each time:',JSON.stringify(attempts));
  const secondTab=await context.newPage();await login(secondTab,name,'Proof-password-12345');const blocked=await secondTab.evaluate(async()=>{try{await connectSip();return false;}catch(e){return e.message.includes('другой вкладке');}});assert(blocked);assert(await student.evaluate(()=>sipUA.isRegistered()));await secondTab.close();console.log('Second browser tab cannot evict the active SIP registration PASS');
  await student.evaluate(async()=>{const card=collectCard();await api(`/api/runs/${state.runId}/finish`,{method:'POST',body:JSON.stringify({...card,incident_type:'Медицинская помощь',address:'город Тула, улица Советская, дом 24',description:'Мужчина потерял сознание, дышит. Нужна скорая помощь.',operator_comment:'Нужна медицинская помощь',services:['103'],victims:true,victims_count:1})});state.runId=null;stopTrainingPhone();});
  const teacher=await adminContext.newPage();await login(teacher,'teacher',process.env.TEACHER_PASSWORD||'teacher12345');await teacher.evaluate(()=>switchView('reports'));await teacher.waitForFunction(id=>state.reports.some(r=>r.id===id),identifier);await teacher.evaluate(id=>openReportDetail(id),identifier);
  const text=await teacher.locator('#report-detail').innerText();for(const title of ['Ответы студента и эталон','Исходное обращение заявителя','Звонки и записи попыток','Реагирование служб','История действий'])assert(text.includes(title),title);
  const detail=await teacher.evaluate(id=>api(`/api/reports/${id}`),identifier);assert.equal(detail.calls.length,attempts.length);assert(detail.calls.every(c=>c.recording_available));await teacher.getByRole('button',{name:'Прослушать запись',exact:true}).first().click();await teacher.locator('#report-detail .report-call audio').waitFor();
  await teacher.locator('#report-detail').screenshot({path:require('node:path').resolve(__dirname,'../../docs/screenshots/teacher-report-details.png')});await teacher.setViewportSize({width:390,height:844});assert(await teacher.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);console.log('Teacher report: source, comparisons, student answers, statuses, timeline, all attempt recordings, mobile, no JS errors PASS');
  fs.writeFileSync(require('node:path').join(require('node:os').tmpdir(),'arm112-calls-reports-proof.json'),JSON.stringify({attempts,report_sections:true,recordings:detail.calls.length,js_errors:errors},null,2));
 }finally{
  if(student){try{await student.evaluate(async()=>{await cancelTrainingCall();if(state.runId)await api(`/api/runs/${state.runId}/skip`,{method:'POST'});if(sipUA)sipUA.stop();});}catch{}}
  if(user){try{await admin.evaluate(id=>api(`/api/users/${id}`,{method:'DELETE'}),user.id);}catch(e){console.log('Cleanup error:',e.message);}}
  await browser.close();
 }
})().catch(e=>{console.error(e);process.exitCode=1});
