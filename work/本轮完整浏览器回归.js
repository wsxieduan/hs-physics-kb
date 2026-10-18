// 复用项目已有的真实点击、历史恢复和文件导入导出回归，不安装浏览器依赖。
const {spawn}=require('node:child_process');
const path=require('node:path');
const fs=require('node:fs');
const root=path.resolve(__dirname,'..');
const profile=path.join(root,'work','_浏览器缓存','产品完整回归20261018');
const child=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',[
  '--headless=new','--disable-gpu','--no-first-run','--disable-background-networking',
  '--remote-debugging-port=9333','--user-data-dir='+profile,'about:blank'
],{windowsHide:true,stdio:'ignore'});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{let connection;try{
  let meta;
  for(let n=0;n<100;n++){try{meta=await(await fetch('http://127.0.0.1:9333/json/version')).json();break;}catch{}await pause(100);}
  if(!meta)throw Error('本轮 Edge 未启动');
  // 原回归假定默认视图是知识点。新版从章节目录开始，测试卡片前需明确切换。
  let script=fs.readFileSync(path.join(__dirname,'机制返工_浏览器核查.js'),'utf8');
  // 旧脚本不断开新标签却不关闭，内存紧张时目标断开还会留下上轮报告。
  // 限制为一个活动标签，所有调用设超时，并在结束后核对新证据确实写出。
  script=script.replace('let ws,seq=0;', 'let activeTarget,ws,seq=0;');
  const callStart=script.indexOf('function call(method,params={})');
  const callEnd=script.indexOf('\nasync function ev(',callStart);
  script=script.slice(0,callStart)+`function call(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>{pending.delete(id);reject(Error('浏览器调用超时：'+method));},20000);pending.set(id,{resolve:r=>{clearTimeout(timer);resolve(r)},reject:e=>{clearTimeout(timer);reject(e)}});ws.send(JSON.stringify({id,method,params}));});}\n`+script.slice(callEnd+1);
  script=script.replace('ws=new WebSocket(tab.webSocketDebuggerUrl);','activeTarget=tab.id;ws=new WebSocket(tab.webSocketDebuggerUrl);');
  script=script.replace('try{ws.close();}catch(e){}',"const previousTarget=activeTarget;activeTarget=t.id;try{ws.close();}catch(e){}");
  script=script.replace("await pause(900);", "if(previousTarget)await fetch('http://127.0.0.1:9333/json/close/'+previousTarget);await pause(900);");
  script=script.replace("await metrics(1200);await nav('quick.html');\n", "await metrics(1200);await nav('quick.html');await click('[data-view=\"points\"]');\n");
  // 同时兼容 Windows 原文件的 CRLF，不修改原回归源码。
  script=script.replace("await metrics(1200);await nav('quick.html');\r\n", "await metrics(1200);await nav('quick.html');await click('[data-view=\"points\"]');\r\n");
  script=script.replace("await nav(file);await metrics(320);await ev(\"document.querySelectorAll('.deep')", "await nav(file);await click('[data-view=\"points\"]');await metrics(320);await ev(\"document.querySelectorAll('.deep')");
  const adapted=path.join(__dirname,'_产品审查完整回归.js');fs.writeFileSync(adapted,script);
  const started=Date.now();
  const tester=spawn(process.execPath,[adapted,
    path.join(root,'outputs','产品审查_2026-10-18','完整交互回归')],{cwd:root,stdio:'inherit',windowsHide:true});
  const exit=await new Promise((resolve,reject)=>{tester.on('exit',resolve);tester.on('error',reject);});
  process.exitCode=exit===0?0:1;
  const receipt=path.join(root,'outputs','产品审查_2026-10-18','完整交互回归','功能核查.json');
  if(!fs.existsSync(receipt)||fs.statSync(receipt).mtimeMs<started)throw Error('回归没有写出本轮新结果，不能引用旧报告');
  const actual=JSON.parse(fs.readFileSync(receipt,'utf8'));
  if(actual.errors.length||actual.rows.some(r=>!r.pass))process.exitCode=1;
  console.log(JSON.stringify({checks:actual.rows.length,passed:actual.rows.filter(r=>r.pass).length,errors:actual.errors.length}));
  connection=new WebSocket(meta.webSocketDebuggerUrl);
  await new Promise(r=>connection.addEventListener('open',r));
  connection.send(JSON.stringify({id:1,method:'Browser.close'}));
  await pause(300);
}catch(e){console.error(e);process.exitCode=1;}finally{if(connection)connection.close();child.kill();}})();
