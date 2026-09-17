const {chromium}=require('playwright');
const assert=require('node:assert/strict');const path=require('node:path');
// Use an isolated fresh database: this proof creates lessons and scenarios.
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try{
  const errors=[];const teacher=await browser.newPage();const student=await browser.newPage({viewport:{width:1440,height:1000}});
  async function login(page,name){page.on('pageerror',e=>errors.push(e.message));await page.goto(process.env.BASE_URL||'http://localhost:18001');await page.locator('#user').fill(name);await page.locator('#pass').fill(name+'12345');await page.locator('#login-submit').click();await page.locator('#workspace').waitFor({state:'visible'});await page.waitForFunction(()=>!$('login-submit').disabled);}
  await login(teacher,'teacher');
  const fixtures=await teacher.evaluate(async()=>{
   const scenarios=[];
   for(let i=1;i<=4;i++){const sc=await api('/api/scenarios',{method:'POST',body:JSON.stringify({title:`Прогресс · Задание ${i}`,category:'Учебный',caller_text:'Меня зовут Иванов Алексей Петрович. По адресу город Тула, улица Советская, дом 24, слышны крики. Нужна полиция.',expected:{incident_type:'Нарушение общественного порядка',address:'город Тула, улица Советская, дом 24',services:['102'],operator_comment:'Слышны крики, нужна полиция',norm_seconds:300}})});scenarios.push(sc.id);await api(`/api/scenarios/${sc.id}/settings`,{method:'PUT',body:JSON.stringify({published:true,mode:'call'})});}
   const lessons=[];for(const [title,ids] of [['Практика · Основное занятие',scenarios.slice(0,3)],['Практика · Второе занятие',scenarios.slice(3)]]){const lesson=await api('/api/lessons',{method:'POST',body:JSON.stringify({title,scenario_ids:ids,student_ids:[3],mode:'call',service_code:'112'})});lessons.push(lesson.id);await api(`/api/lessons/${lesson.id}/start`,{method:'POST'});}return {scenarios,lessons};
  });
  await login(student,'student');await student.evaluate(()=>switchView('training'));
  const lesson=id=>student.locator(`#assigned-lessons [data-lesson-id="${id}"]`);
  const row=(l,s)=>lesson(l).locator(`[data-scenario-id="${s}"]`);
  const [first,second]=fixtures.lessons;const [a,b,c,d]=fixtures.scenarios;
  await lesson(first).locator('summary').click();await lesson(second).locator('summary').click();
  await row(first,a).getByRole('button',{name:'Начать',exact:true}).click();await student.waitForFunction(()=>state.runId&&!state.busy);
  const run=await student.evaluate(()=>state.runId);await student.locator('#description').fill('Сохранённые сведения о заявителе');await student.waitForTimeout(1100);
  assert(await lesson(second).locator('.lesson-progress-heading button').isDisabled());
  const blocked=await student.evaluate(async id=>{try{return await api(`/api/lessons/${id}/next`,{method:'POST'});}catch(e){return {error:e.message};}},second);assert(blocked.error.includes('другого занятия'));
  await student.locator('#skip-card').click();await student.waitForFunction(()=>!state.runId&&!state.busy);
  assert.equal(await row(first,a).getAttribute('data-task-status'),'skipped');
  const saved=await student.evaluate(id=>api(`/api/runs/${id}`),run);assert(saved.report.skipped);
  await row(first,b).getByRole('button',{name:'Начать',exact:true}).click();await student.waitForFunction(()=>state.runId&&!state.busy);
  async function finish(){await student.locator('#description').fill('Слышны крики, нужна полиция');await student.locator('#incident').fill('Нарушение общественного порядка');await student.locator('#address').fill('город Тула, улица Советская, дом 24');await student.locator('#finish-button').click();await student.waitForFunction(()=>!state.runId&&!state.busy);}
  await finish();assert.equal(await row(first,b).getAttribute('data-task-status'),'completed');
  assert(await student.locator('#result').getByRole('button',{name:'Задание выполнено',exact:true}).isDisabled());
  await student.evaluate(()=>document.querySelector('.scenario-panel').classList.remove('collapsed'));
  await student.locator('#scenario-search').fill('Задание 2');
  const completedScenario=student.locator(`#scenarios [data-scenario-id="${b}"]`);
  assert((await completedScenario.innerText()).includes('✓ Выполнено'));
  assert(await completedScenario.getByRole('button',{name:'Выполнено',exact:true}).isDisabled());
  const blockedRepeat=await student.evaluate(async id=>{const r=await fetch(`/api/runs/${id}/start`,{method:'POST',headers:{Authorization:'Bearer '+state.token}});return r.status;},b);assert.equal(blockedRepeat,409);
  await student.evaluate(id=>choose(state.scenarios.find(s=>s.id===id)),b);assert(await student.locator('#call').isHidden());
  await completedScenario.getByRole('button',{name:'Результат',exact:true}).click();await student.locator('#report-detail .report-heading h2').waitFor();
  await student.evaluate(()=>switchView('training'));await student.locator('#scenario-search').fill('');
  await row(first,b).getByRole('button',{name:'Результат',exact:true}).click();await student.locator('#report-detail .report-heading h2').waitFor();await student.evaluate(()=>switchView('training'));
  await row(first,c).getByRole('button',{name:'Начать',exact:true}).click();await student.waitForFunction(()=>state.runId&&!state.busy);
  await row(first,c).locator('.lesson-task-status.in_progress').waitFor();
  const statuses=await student.locator('#assigned-lessons .lesson-task-status').allTextContents();assert(statuses.includes('В работе')&&statuses.includes('Выполнено')&&statuses.includes('Пропущено')&&statuses.includes('Не начато'));
  await student.locator('#assigned-lessons').screenshot({path:path.resolve(__dirname,'../../docs/screenshots/student-lesson-progress.png')});
  await student.setViewportSize({width:390,height:1000});assert(await student.locator('#assigned-lessons').evaluate(n=>n.scrollWidth<=n.clientWidth));await student.setViewportSize({width:1440,height:1000});
  await finish();await row(first,a).getByRole('button',{name:'Продолжить',exact:true}).click();await student.waitForFunction(()=>state.runId&&!state.busy);
  assert.equal(await student.evaluate(()=>state.runId),run);assert.equal(await student.locator('#description').inputValue(),'Сохранённые сведения о заявителе');const resumed=await student.evaluate(id=>api(`/api/runs/${id}`),run);assert(resumed.elapsed_seconds>=saved.elapsed_seconds&&resumed.elapsed_seconds<=saved.elapsed_seconds+3);
  await finish();assert((await lesson(first).innerText()).includes('Все задания выполнены'));assert(await lesson(first).locator('.lesson-progress-heading button').isDisabled());
  await student.evaluate(id=>nextLesson(id),first);assert(await student.locator('#incident-form').isHidden());assert(await student.evaluate(()=>state.runId===null&&state.startedAt===null));
  const timer=await student.locator('#timer').textContent();await student.waitForTimeout(1200);assert.equal(await student.locator('#timer').textContent(),timer);
  await lesson(second).locator('.lesson-progress-heading button').click();await student.waitForFunction(()=>state.runId&&!state.busy);await finish();assert((await lesson(second).innerText()).includes('Все задания выполнены'));
  assert.deepEqual(errors,[]);console.log('Lesson tasks: all four statuses, cross-lesson guard, preserved skip/resume time and answers, own report, stopped completion timer, completed scenario badge/search, blocked UI/API repeat, open dropdown and mobile PASS');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
