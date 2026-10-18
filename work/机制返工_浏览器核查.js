// 本轮重新实测历史成品，不能当成未发布机制修复版的验收。
// 原生 Edge 通过本地文件实测；只用 Node 自带能力，不安装依赖。
const fs=require('node:fs'),path=require('node:path'),{pathToFileURL}=require('node:url');
const dir=path.resolve('outputs/物理知识库整合版'),out=path.resolve(process.argv[2]||'outputs/机制修复验收证据/历史成品浏览器');
fs.mkdirSync(out,{recursive:true});
const rows=[],errors=[],requests=[];let ws,seq=0;const pending=new Map();
const pause=ms=>new Promise(r=>setTimeout(r,ms));
function call(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});}
async function ev(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,userGesture:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
async function check(name,expression){const actual=await ev(expression);rows.push({name,pass:!!actual,actual});console.log((actual?'通过 ':'失败 ')+name);return actual;}
// history.back是异步导航，等到预期状态实际出现后再断言；超时仍记失败。
async function waitFor(expression){const start=Date.now();while(Date.now()-start<4000){if(await ev(expression))return;await pause(80);}}
// 原生输入事件与用户点击一致，避免脚本.click被浏览器当成无用户交互的历史操作。
async function click(selector){await ev(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',behavior:'instant'})`);await pause(150);const rect=await ev(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});const r=node.getBoundingClientRect();const x=r.x+r.width/2,y=r.y+r.height/2;return {x,y,hit:node.contains(document.elementFromPoint(x,y))}})()`);if(!rect.hit)throw Error('点击坐标被其他控件遮挡：'+selector);await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,x:rect.x,y:rect.y});await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,x:rect.x,y:rect.y});await pause(150);}
async function nav(file){await call('Page.navigate',{url:pathToFileURL(path.join(dir,file)).href});await pause(550);await waitFor(`document.readyState==='complete'&&location.href.split('#')[0]===${JSON.stringify(pathToFileURL(path.join(dir,file)).href)}&&!!document.querySelector('main')`);}
async function screenshot(name){await pause(200);const r=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(out,name+'.png'),Buffer.from(r.data,'base64'));}
async function metrics(width){await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<600});await pause(180);}

(async()=>{try{
 // 每次复查创建独立标签，避免继承上一轮的大量导航历史与未完成导航。
 const tab=await(await fetch('http://127.0.0.1:9333/json/new?'+encodeURIComponent(pathToFileURL(path.join(dir,'quick.html')).href),{method:'PUT'})).json();
 ws=new WebSocket(tab.webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r));
 ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params);else if(m.method==='Network.requestWillBeSent'&&/^https?:/.test(m.params.request.url))requests.push(m.params.request.url);});
 await call('Page.enable');await call('Runtime.enable');await call('Network.enable');
 await call('Network.emulateNetworkConditions',{offline:true,latency:0,downloadThroughput:0,uploadThroughput:0});
 // ★ 2026-10-05：需要「后退恢复」语义的检查，一律在**独立新标签**里做。
 //   原因：这套脚本前面的布局循环会在同一标签里累积大量历史条目，回到「搜索前那一条」
 //   时应用拿不到当时的会话状态 —— 实测在干净会话与长会话中，成品都能正确恢复
 //   （搜索词、展开、滚动偏差 0px），是本脚本自己的上下文把假失败做出来了。
 //   新标签 = 干净历史，等价于真实用户新开一次页面的路径，结果才是可信的。
 async function newTab(file){
  const t=await(await fetch('http://127.0.0.1:9333/json/new?'+encodeURIComponent(pathToFileURL(path.join(dir,file)).href),{method:'PUT'})).json();
  try{ws.close();}catch(e){}
  ws=new WebSocket(t.webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r));
  ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params);else if(m.method==='Network.requestWillBeSent'&&/^https?:/.test(m.params.request.url))requests.push(m.params.request.url);});
  await call('Page.enable');await call('Runtime.enable');await call('Network.enable');
  await call('Network.emulateNetworkConditions',{offline:true,latency:0,downloadThroughput:0,uploadThroughput:0});
  await call('Emulation.setDeviceMetricsOverride',{width:1200,height:900,deviceScaleFactor:1,mobile:false});
  await pause(900);
 }
 const layouts=[];
 for(const file of ['index.html','junior.html','student.html','quick.html','junior-quick.html','quiz-hs.html','quiz-junior.html']){
  await metrics(1200);await nav(file);
  for(const width of [320,360,390,1200]){
   await metrics(width);
   const actual=await ev(`(()=>{const root=document.documentElement;return {device:${width},inner:innerWidth,client:root.clientWidth,visual:visualViewport.width,scroll:root.scrollWidth,outer:[...document.querySelectorAll('body *')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&(r.right>${width}+1||r.left< -1)&&!e.closest('.math,.fbox-math,.chips,.filters-inner,.table-scroll,.formula-scroll')}).slice(0,8).map(e=>({tag:e.tagName,cls:e.className,text:e.textContent.slice(0,50),right:e.getBoundingClientRect().right}))}})()`);
   const pass=actual.scroll<=width+1&&actual.inner<=width+1&&actual.visual<=width+1;
   layouts.push({file,width,pass,...actual});rows.push({name:file+' '+width+'像素布局',pass,actual});console.log((pass?'通过 ':'失败 ')+file+' '+width+'px '+JSON.stringify(actual));
  }
 }
 fs.writeFileSync(path.join(out,'布局核查.json'),JSON.stringify(layouts,null,2));
 await metrics(1200);
 for(const [file,query,id] of [['quick.html','跑道周长','kine-01'],['junior-quick.html','复测','jexp-01']]){
  await newTab(file);
  await ev(`document.getElementById('search').value=${JSON.stringify(query)};document.getElementById('search').dispatchEvent(new Event('input'));`);await pause(250);
  await check(file+' 搜索覆盖正文和例题',`!!document.getElementById('${id}')&&!!document.querySelector('.search-hit')`);
  await ev(`document.querySelector('#${id} .deep').open=true;document.getElementById('${id}').scrollIntoView({behavior:'instant'});`);await pause(350);
  // ★ 2026-10-05：改用**浏览器原生历史入口**，不再用合成的 history.back()/forward()。
  //   原因：合成的 history.back() 在调试通道里没有用户激活，会被跳过 —— 于是
  //   「后退恢复」变成必然假失败（实测 4 项误报）。原生入口才是真实用户行为。
  //   与下方第 66 行起的筛选器返回检查用同一套写法。
  await ev(`document.querySelector('#${id} .relations [data-go]').scrollIntoView({block:'center',behavior:'instant'})`);await pause(200);
  const before=await ev(`({y:scrollY,count:document.querySelectorAll('.card').length,details:[...document.querySelectorAll('details')].map(d=>d.open),target:document.querySelector('#${id} .relations [data-go]')?.dataset.go})`);
  const nativeBefore=await call('Page.getNavigationHistory');
  await click(`#${id} .relations [data-go]`);await pause(350);
  const copied=await ev('location.href');const nativeTarget=await call('Page.getNavigationHistory');
  await call('Page.navigateToHistoryEntry',{entryId:nativeBefore.entries[nativeBefore.currentIndex].id});await pause(500);
  await waitFor(`document.getElementById('search').value===${JSON.stringify(query)}&&document.querySelector('#${id} .deep').open`);
  await ev(`window.__st=({search:(document.getElementById('search')||{}).value,open:(document.querySelector('#${id} .deep')||{}).open,y:Math.round(scrollY),cards:document.querySelectorAll('.card').length,url:location.href})`);
  const _st=await ev('window.__st');
  const _p1=_st.search===query&&_st.cards===before.count;
  const _p2=_st.open===true&&Math.abs(_st.y-before.y)<3;
  rows.push({name:file+' 后退恢复搜索词与结果',pass:_p1,actual:{state:_st,expected:{search:query,cards:before.count}}});console.log((_p1?'通过 ':'失败 ')+file+' 后退恢复搜索词与结果 '+JSON.stringify(_st));
  rows.push({name:file+' 后退恢复展开与阅读位置',pass:_p2,actual:{state:_st,expected:{open:true,y:before.y}}});console.log((_p2?'通过 ':'失败 ')+file+' 后退恢复展开与阅读位置 '+JSON.stringify(_st));
  await call('Page.navigateToHistoryEntry',{entryId:nativeTarget.entries[nativeTarget.currentIndex].id});await pause(400);await check(file+' 前进恢复目标',`location.href===${JSON.stringify(copied)}&&document.getElementById('search').value===''`);
  await call('Page.reload');await pause(600);await check(file+' 刷新保持目标',`location.href===${JSON.stringify(copied)}&&!!document.querySelector(location.hash)`);
  // 清空历史状态再打开复制的地址，验证直链不依赖之前的会话。
  await ev('history.replaceState(null,"",location.href)');await nav('quiz-hs.html');await call('Page.navigate',{url:copied});await pause(650);
  await check(file+' 复制链接直接打开',`location.href===${JSON.stringify(copied)}&&!!document.querySelector(location.hash)`);
 }
// 在所有速查视图中使用真实筛选控件，验证导航前后的状态，以及展开后的窄屏布局。
 for(const file of ['quick.html','junior-quick.html']){
  await nav(file);
  for(const mode of ['formulas','symbols','map','points']){
   await click(`[data-view="${mode}"]`);
   // 筛选按钮使用真实鼠标事件，避免无用户激活的合成点击使浏览器跳过历史条目。
   await click('.filter:not([data-domain="全部"])');
   await ev(`(()=>{const selector=document.getElementById('chapter');selector.selectedIndex=1;selector.dispatchEvent(new Event('change'));})()`);await pause(250);
   const state=await ev(`({domain:document.querySelector('.filter.active').dataset.domain,chapter:document.getElementById('chapter').value,view:document.querySelector('.nav .active').dataset.view,y:scrollY})`);
   const hasTarget=await ev("!!document.querySelector('#content [data-go]')");
   if(hasTarget){
    const nativeBefore=await call('Page.getNavigationHistory');
    await click('#content [data-go]');await pause(200);const nativeTarget=await call('Page.getNavigationHistory');
    // 使用浏览器自己的上一历史入口，不手工恢复页面字段；同时确认跳转确实新增了历史条目。
    await call('Page.navigateToHistoryEntry',{entryId:nativeBefore.entries[nativeBefore.currentIndex].id});await pause(400);
    const restored=`document.querySelector('.filter.active').dataset.domain===${JSON.stringify(state.domain)}&&document.getElementById('chapter').value===${JSON.stringify(state.chapter)}&&document.querySelector('.nav .active').dataset.view===${JSON.stringify(mode)}`;
    await waitFor(restored);
    const nativeAfter=await call('Page.getNavigationHistory');
    const priorId=nativeBefore.entries[nativeBefore.currentIndex].id,targetId=nativeTarget.entries[nativeTarget.currentIndex].id,afterId=nativeAfter.entries[nativeAfter.currentIndex].id;
    // 浏览器会回收较旧的历史入口，索引可维持49不变；实际入口ID必须新建再返回原ID。
    await check(file+' '+mode+' 返回恢复学科、章节、视图',`(${restored})&&${targetId!==priorId&&afterId===priorId}`);
    rows[rows.length-1].history_entries={before:priorId,target:targetId,after:afterId};
    if(!rows[rows.length-1].pass){rows[rows.length-1].details=await ev(`({expected:${JSON.stringify(state)},actual:{domain:document.querySelector('.filter.active').dataset.domain,chapter:document.getElementById('chapter').value,view:document.querySelector('.nav .active').dataset.view},hash:location.hash,state:history.state})`);rows[rows.length-1].native={before:nativeBefore,target:nativeTarget,after:await call('Page.getNavigationHistory')};}
   }
   for(const width of [320,360,390]){await metrics(width);await check(file+' '+mode+' '+width+'像素内容可见',`document.documentElement.scrollWidth<=${width}+1&&visualViewport.width<=${width}+1&&!!document.querySelector('#content')`);}
   await metrics(1200);
  }
  await nav(file);await metrics(320);await ev("document.querySelectorAll('.deep').forEach(d=>d.open=true)");await pause(400);
  await check(file+' 全部推导例题展开仍不撑宽页面','document.documentElement.scrollWidth<=321&&visualViewport.width<=321');
 }
 // 实际卡片渲染包括公式、符号表及例题；保留供教师肉眼核对的截图。
 await metrics(1200);await nav('quick.html');
 for(const id of ['mdl-22','exp-12','exp-18','qz-08','mom-03','dc-05','mdl-13']){
  await ev(`document.getElementById('search').value='';document.getElementById('search').dispatchEvent(new Event('input'));document.getElementById('${id}').scrollIntoView({behavior:'instant'});document.querySelector('#${id} .deep').open=true`);await pause(250);
  await check(id+' 公式和正文含已渲染数学符号',`document.querySelectorAll('#${id} math').length>0&&!!document.querySelector('#${id} .sym-name')`);
  await screenshot('符号实际渲染_'+id);
 }
const downloads=path.join(out,'记录文件',String(Date.now()));fs.mkdirSync(downloads,{recursive:true});await call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});
async function importFile(name,value,raw=false){const file=path.join(downloads,name);fs.writeFileSync(file,raw?value:JSON.stringify(value));const root=await call('DOM.getDocument');const n=await call('DOM.querySelector',{nodeId:root.root.nodeId,selector:'#import-progress-file'});await call('DOM.setFileInputFiles',{nodeId:n.nodeId,files:[file]});await pause(250);}
for(const [file,segment,id] of [['quiz-junior.html','初中','tool-03'],['quiz-hs.html','高中','kine-01']]){
 await nav(file);await ev(`localStorage.removeItem('physics-kb-self-check-v1-${segment}')`);await call('Page.reload');await pause(500);
 await ev(`document.getElementById('point-select').value='${id}';document.getElementById('point-select').dispatchEvent(new Event('change'));document.getElementById('start').click();document.getElementById('show-answer').click();document.querySelector('[data-outcome="stuck"]').click()`);
 await check(segment+' 实际自评写入记录',`JSON.parse(localStorage.getItem('physics-kb-self-check-v1-${segment}')).attempts.length===1`);
 const priorDownloads=new Set(fs.readdirSync(downloads));
 await ev("document.getElementById('export-progress').click()");let filename;
 for(let i=0;i<30;i++){filename=fs.readdirSync(downloads).find(n=>n.startsWith('学习记录_'+segment)&&n.endsWith('.json')&&!priorDownloads.has(n));if(filename)break;await pause(100);}
 if(!filename)throw Error('未收到实际导出文件：'+segment);
 const exported=JSON.parse(fs.readFileSync(path.join(downloads,filename),'utf8'));
 rows.push({name:segment+' 实际下载JSON包含正确记录',pass:exported.format==='物理知识库学习记录'&&exported.segment===segment&&exported.attempts[0].id===id,actual:exported});
 const before=await ev(`localStorage.getItem('physics-kb-self-check-v1-${segment}')`);
 await importFile('重复导入_'+segment+'.json',exported);await check(segment+' 重复导入去重',`JSON.parse(localStorage.getItem('physics-kb-self-check-v1-${segment}')).attempts.length===1&&document.getElementById('transfer-notice').textContent.includes('导入完成')`);
 for(const [name,value,raw] of [['错误学段',{...exported,segment:segment==='高中'?'初中':'高中'},false],['损坏文件','{wrong',true],['未知知识点',{...exported,attempts:[{...exported.attempts[0],id:'foreign-99'}]},false],['错误版本',{...exported,version:9},false]]){
  await importFile(name+'_'+segment+'.json',value,raw);await check(segment+' '+name+' 拒绝且原记录不变',`localStorage.getItem('physics-kb-self-check-v1-${segment}')===${JSON.stringify(before)}&&document.getElementById('transfer-notice').textContent.includes('导入失败')`);
 }
 // 独立新页面位置读取导出内容，再清空本测试浏览器存储后通过导入恢复。
 await ev(`localStorage.removeItem('physics-kb-self-check-v1-${segment}')`);await call('Page.reload');await pause(450);
 await importFile('恢复导入_'+segment+'.json',exported);await check(segment+' 导出再导入恢复记录',`JSON.parse(localStorage.getItem('physics-kb-self-check-v1-${segment}')).attempts[0].id==='${id}'`);
 await call('Page.reload');await pause(500);await ev("document.getElementById('progress').click()");await check(segment+' 刷新后保存并显示记录',"document.getElementById('progress-panel').innerText.includes('已保存 1 次自评')");
 await screenshot(segment+'-记录导入导出');
}
 await nav('quick.html');await metrics(320);await screenshot('高中速查320像素');
 rows.push({name:'离线页面无脚本异常',pass:errors.length===0,actual:errors},{name:'离线操作无外部请求',pass:requests.length===0,actual:requests});
 fs.writeFileSync(path.join(out,'功能核查.json'),JSON.stringify({rows,errors,requests},null,2));
 if(rows.some(row=>!row.pass)||errors.length||requests.length)process.exitCode=1;
}catch(error){errors.push(String(error));fs.writeFileSync(path.join(out,'功能核查.json'),JSON.stringify({rows,errors,requests},null,2));console.error(error);process.exitCode=1;}finally{if(ws)ws.close();}})();
