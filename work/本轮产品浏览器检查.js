// 通过系统 Edge 检查真实离线页面；仅用 Node 标准库，缓存放在 D 盘。
const fs = require('node:fs'), path = require('node:path');
const {spawn} = require('node:child_process');
const {pathToFileURL} = require('node:url');
const root = path.resolve(__dirname, '..');
const phase = process.argv[2] || '改前';
const dest = path.join(root, 'outputs', '产品审查_2026-10-18', phase);
fs.mkdirSync(dest, {recursive:true});
const profile = path.join(root, 'work', '_浏览器缓存', '产品审查20261018');
const browser = spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
   '--disable-background-networking', '--remote-debugging-port=9348', '--user-data-dir='+profile, 'about:blank'],
  {windowsHide:true, stdio:'ignore'});
const sleep = ms => new Promise(r=>setTimeout(r,ms));
const pending = new Map(); let socket, serial=0;
const rows=[], errors=[], external=[];
function call(method,params={}) {return new Promise((resolve,reject)=>{
  const id=++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));
  setTimeout(()=>{if(pending.has(id)){pending.delete(id);reject(Error('浏览器调用超时：'+method));}},15000).unref();
});}
async function ev(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,userGesture:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
async function check(name,expression){const actual=await ev(expression);rows.push({name,pass:!!actual,actual});console.log((actual?'通过 ':'失败 ')+name);}
async function nav(file,hash=''){
  // 每个独立场景从新阅读状态开始；刷新保留状态的用例另行使用 Page.reload。
  await ev("history.replaceState(null,'',location.href)");
  const url=pathToFileURL(path.join(root,'site',file)).href+hash;
  await call('Page.navigate',{url});
  for(let i=0;i<70;i++){await sleep(100);if(await ev(`document.readyState==='complete'&&location.href===${JSON.stringify(url)}&&!!document.querySelector('main')`))break;}
  await sleep(250);
}
async function query(value){await ev(`document.getElementById('search').value=${JSON.stringify(value)};document.getElementById('search').dispatchEvent(new Event('input',{bubbles:true}));`);await sleep(150);}
async function shot(name){const r=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(dest,name+'.png'),Buffer.from(r.data,'base64'));}
(async()=>{try{
  let ready=false;
  for(let i=0;i<100;i++){try{await fetch('http://127.0.0.1:9348/json/version');ready=true;break;}catch{}await sleep(100);}
  if(!ready)throw Error('Edge 未能启动');
  const tab=await(await fetch('http://127.0.0.1:9348/json/new?about:blank',{method:'PUT'})).json();
  socket=new WebSocket(tab.webSocketDebuggerUrl);await new Promise(r=>socket.addEventListener('open',r));
  socket.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params);else if(m.method==='Network.requestWillBeSent'&&/^https?:/.test(m.params.request.url))external.push(m.params.request.url);});
  await call('Page.enable');await call('Runtime.enable');await call('Network.enable');
  await call('Network.emulateNetworkConditions',{offline:true,latency:0,downloadThroughput:0,uploadThroughput:0});
  for(const file of ['index.html','junior.html','student.html','quick.html','junior-quick.html','quiz-hs.html','quiz-junior.html']){
    await nav(file);
    for(const width of [320,390,1280]){
      await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<600});await sleep(160);
      await check(file+' '+width+'像素无横向溢出',`document.documentElement.scrollWidth<=${width}+1`);
      if(['quick.html','junior-quick.html','index.html'].includes(file)&&width!==390)await shot(file.replace('.html','')+'-'+width);
    }
  }
  for(const [file,searches] of [['quick.html',[['牛顿 第二定律','dyn-05'],['平抛 速度',null],['单摆 周期','osc-03'],['跑道周长','kine-01']]],['junior-quick.html',[['欧姆 电阻',null],['电流 mA',null],['复测','jexp-01']]]]){
    await nav(file);
    for(const [q,id] of searches){await query(q);await check(file+' 搜索“'+q+'”',id?`!!document.getElementById(${JSON.stringify(id)})`:`document.querySelectorAll('#content .card').length>0`);}
    await query('');
    await ev("document.querySelector('[data-view=\"formulas\"]').click()");await query('m/s');
    await check(file+' 公式可按单位搜索',"document.querySelectorAll('#content .index-card').length>0");
    await ev("document.querySelector('[data-view=\"symbols\"]').click()");await query('kg');
    await check(file+' 符号可按单位搜索',"document.querySelectorAll('#content .symbol-card').length>0");
    if(phase!=='改前'){
      await nav(file);
      await check(file+' 默认展示章节重点',"document.querySelectorAll('[data-chapter-go]').length>15");
      const catalog = await ev("JSON.parse(document.getElementById('kb-data').textContent).g.map(g=>({name:g[0],order:g[2],count:g[3].length}))");
      for(const group of catalog){
        await ev(`document.getElementById('chapter').value=${JSON.stringify(group.name)};document.getElementById('chapter').dispatchEvent(new Event('change'));`);
        await check(file+' 章节 '+group.order+' 概览覆盖本章全部知识点',`document.querySelectorAll('.outline-point').length===${group.count}`);
      }
      await nav(file);
      await ev("document.querySelector('[data-chapter-go]').click()");
      await check(file+' 本章概览与知识点对应',"!!document.querySelector('.chapter-overview')&&document.querySelectorAll('.outline-point').length>0");
      await shot(file.replace('.html','')+'-章节重点');
      await call('Page.reload');await sleep(600);
      await check(file+' 刷新保留章节',"!!document.querySelector('.chapter-overview')&&document.getElementById('chapter').value!=='全部'");
      await ev("document.querySelector('[data-view=\"points\"]').click()");
      await query('完全不存在的物理术语20261018');
      await check(file+' 无结果提供重置操作',"!document.getElementById('empty').hidden&&!!document.getElementById('reset-filters')");
      await ev("document.getElementById('reset-filters').click()");
      await check(file+' 重置清除搜索和筛选',"document.getElementById('search').value===''&&document.getElementById('chapter').value==='全部'");
      await ev("document.querySelector('[data-view=\"chapters\"]').click();document.querySelector('.filter:not([data-domain=\"全部\"])').click();document.getElementById('chapter').selectedIndex=15;document.getElementById('chapter').dispatchEvent(new Event('change'));");
      await check(file+' 跨领域选择章节不会产生冲突空结果',"document.querySelectorAll('.outline-point').length>0");
      await ev("document.querySelector('.filter:not([data-domain=\"全部\"])').click()");
      await check(file+' 切换领域会解除冲突章节',"!!document.querySelector('.chapter-card,.chapter-overview')");
      await nav(file,'#points');await query(file==='quick.html'?'牛顿 第二定律':'欧姆 电阻');
      await check(file+' 关键词标题匹配优先',file==='quick.html'?"document.querySelector('.card').id==='dyn-05'":"document.querySelector('.card h2').textContent.includes('欧姆')");
    }
  }
  if(phase!=='改前'){
    for(const file of ['index.html','student.html','junior.html']){
      await nav(file);const q=file==='junior.html'?'欧姆 电阻':'牛顿 第二定律';
      await ev(`document.getElementById('q').value=${JSON.stringify(q)};document.getElementById('q').dispatchEvent(new Event('input'));`);
      await check(file+' 完整内容也支持复合检索',"[...document.querySelectorAll('.kp')].some(c=>c.style.display!=='none')");
    }
    await nav('student.html');await ev("document.querySelector('.views [data-view=\"formula\"]').click();document.getElementById('q').value='完全不存在的术语';document.getElementById('q').dispatchEvent(new Event('input'))");
    await check('学生版公式索引真正应用搜索',"![...document.querySelectorAll('.vf')].some(c=>c.style.display!=='none')&&document.getElementById('empty').style.display!=='none'");
    await ev("document.querySelector('.views [data-view=\"symbol\"]').click()");
    await check('学生版符号索引真正应用搜索',"![...document.querySelectorAll('.vsrow')].some(c=>c.style.display!=='none')");
  }
  fs.writeFileSync(path.join(dest,'浏览器结果.json'),JSON.stringify({rows,errors,external},null,2));
  console.log(JSON.stringify({total:rows.length,passed:rows.filter(x=>x.pass).length,errors:errors.length,external:external.length}));
  if(phase!=='改前'&&(rows.some(x=>!x.pass)||errors.length||external.length))process.exitCode=1;
}catch(e){console.error(e);process.exitCode=1;}finally{
  try{if(socket){await call('Browser.close');socket.close();}}catch{}
  browser.kill();
}})();
