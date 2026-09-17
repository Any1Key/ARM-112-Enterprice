const {chromium}=require('playwright');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const crypto=require('node:crypto');
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});let admin,student,user;try{
 const errors=[];const adminContext=await browser.newContext();admin=await adminContext.newPage();
 async function login(page,name,password){page.on('pageerror',e=>errors.push(e.message));await page.goto(process.env.BASE_URL||'http://localhost:8000');await page.locator('#user').fill(name);await page.locator('#pass').fill(password);await page.locator('#login-submit').click();await page.locator('#workspace').waitFor({state:'visible'});await page.waitForFunction(()=>!$('login-submit').disabled);}
 await login(admin,'admin',process.env.ADMIN_PASSWORD||'admin12345');const username='materials_help_proof_'+Date.now();user=await admin.evaluate(username=>api('/api/users',{method:'POST',body:JSON.stringify({username,password:'Proof-password-12345',role:'student'})}),username);
 const context=await browser.newContext({acceptDownloads:true});student=await context.newPage({viewport:{width:1440,height:1000}});await login(student,username,'Proof-password-12345');
 let pdfRequests=0;student.on('request',r=>{if(r.url().includes('/api/materials/source/manual'))pdfRequests++;});
 await student.locator('[data-view="materials"]').click();await student.locator('#trainer-guide .guide-section').first().waitFor();
 assert.equal(await student.locator('#trainer-guide .guide-section').count(),9);assert(await student.locator('#classifier-summary').isHidden());assert(await student.locator('#ticket-controls').isHidden());
 const guide=await student.locator('#trainer-guide').innerText();for(const text of ['Пропуск, возврат и таймер','Действия ДДС','Внешний SIP-телефон','Результат и оценка'])assert(guide.includes(text),text);
 await student.locator('#materials-view').screenshot({path:path.resolve(__dirname,'../../docs/screenshots/student-materials-guide.png')});
 await student.locator('#read-manual').click();await student.waitForFunction(()=>$('manual-frame').getAttribute('src')?.startsWith('blob:'));
 assert(await student.locator('#manual-reader').isVisible());const url=await student.locator('#manual-frame').getAttribute('src');assert.equal(await student.locator('#manual-new-tab').getAttribute('href'),url);
 const downloaded=student.waitForEvent('download');await student.locator('#download-manual').click();const download=await downloaded;assert(download.suggestedFilename().endsWith('.pdf'));const file=fs.readFileSync(await download.path());assert(file.subarray(0,5).toString()==='%PDF-');const original=fs.readFileSync(path.resolve(__dirname,'../../source_materials/Работа с АРМ-112 для ДДС от ОКр_ГСИ.pdf'));assert.equal(crypto.createHash('sha256').update(file).digest('hex'),crypto.createHash('sha256').update(original).digest('hex'));
 await student.locator('#close-manual').click();assert(await student.locator('#manual-reader').isHidden());await student.locator('#open-manual').click();await student.locator('#manual-reader').waitFor();assert.equal(pdfRequests,1,'The same authenticated PDF should be reused');
 const privateStatus=await student.evaluate(async()=>{const r=await fetch('/api/materials/source/tickets',{headers:{Authorization:'Bearer '+state.token}});return r.status;});assert.equal(privateStatus,403);
 await student.setViewportSize({width:390,height:1000});assert(await student.locator('#materials-view').evaluate(n=>n.scrollWidth<=n.clientWidth));
 await student.locator('#logout').click();await student.locator('#login-screen').waitFor();assert.equal(await student.locator('#manual-frame').getAttribute('src'),null);assert.deepEqual(errors,[]);
 console.log('Student guide, 9 help topics, inline authenticated PDF, exact download, cached reopen, private tickets, logout cleanup and mobile PASS');
 }finally{if(user&&admin)try{await admin.evaluate(id=>api(`/api/users/${id}`,{method:'DELETE'}),user.id);}catch(e){console.error('Cleanup:',e.message);process.exitCode=1;}await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
