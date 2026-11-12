'use strict';
const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const busyStates = new Set(['queued','running','capturing','analyzing','speaking']);
let selected = null, selectedDetail = null, latest = null, followLatest = true;
let older = [], cursor = null, olderLoaded = false, view = 'workspace', mode = 'live';
let syncing = false, submitting = false, pendingPhoto = null;
let detailSignature = '', historySignature = '', activitySignature = '', recentSignature = '', photoId = null;
let liveEnabled = true, livePending = false, liveTimer = null, frameLoaded = false, lastFrameAt = 0;
let flowDetail = null;
const stageLabels = {queued:'Queued',running:'Processing',capturing:'Capturing',analyzing:'Analysing',speaking:'Responding',done:'Complete',failed:'Failed',interrupted:'Interrupted'};
function node(tag, cls, text) {const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;}
function icon(name) {const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','#'+name);svg.append(use);return svg;}
function date(value, onlyTime=false) {return value?new Date(value).toLocaleString([],onlyTime?{hour:'2-digit',minute:'2-digit',second:'2-digit'}:{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'';}
function elapsed(start,end) {return Math.max(0,(new Date(end||Date.now())-new Date(start))/1000);}
function duration(seconds) {return seconds<10?seconds.toFixed(1)+'s':Math.round(seconds)+'s';}
function setText(id, text) {if($(id).textContent!==String(text))$(id).textContent=text;}
function photoUrl(id) {return '/photos/'+id+'.jpg';}
async function api(path, options={}) {
  const response=await fetch(path,{credentials:'same-origin',...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...options.headers}});
  if(response.status===401){location.href='/login';throw new Error('Please sign in.');}
  let data={};try{data=await response.json();}catch{}
  if(!response.ok){const error=new Error(data.error||'Request failed. Please try again.');error.status=response.status;throw error;}
  return data;
}
function report(error){$('request-error').hidden=false;setText('request-error',error.message);}
function setView(next) {
  view=next;
  document.querySelectorAll('.view').forEach(e=>e.hidden=e.id!==next+'-view');
  document.querySelectorAll('[data-view]').forEach(e=>{e.classList.toggle('active',e.dataset.view===next);e.setAttribute('aria-current',e.dataset.view===next?'page':'false');});
  const titles={workspace:['Camera workspace','Live view, scene descriptions and everything in between.','Overview'],sessions:['Session history','Browse saved photos, commands and responses.','Sessions'],device:['Device overview','Camera, audio and processes running on your Raspberry Pi.','Device']};
  setText('page-title',titles[next][0]);setText('page-subtitle',titles[next][1]);setText('view-breadcrumb',titles[next][2]);
  clearTimeout(liveTimer);
  if(next==='workspace'&&mode==='live'&&liveEnabled)requestFrame();
}
function setMode(next) {
  mode=next;$('view-live').classList.toggle('selected',next==='live');$('view-captured').classList.toggle('selected',next==='captured');
  clearTimeout(liveTimer);renderCamera();
  if(next==='live'&&liveEnabled)requestFrame();
}
function liveWanted(){return liveEnabled&&view==='workspace'&&mode==='live'&&!document.hidden;}
function requestFrame() {
  clearTimeout(liveTimer);
  if(!liveWanted()||livePending)return;
  livePending=true;$('live-image').src='/api/camera/frame?t='+Date.now();
}
$('live-image').addEventListener('load',()=>{
  livePending=false;frameLoaded=true;lastFrameAt=Date.now();renderCamera();
  if(liveWanted())liveTimer=setTimeout(requestFrame,200);
});
$('live-image').addEventListener('error',()=>{
  livePending=false;frameLoaded=false;renderCamera();
  if(liveWanted())liveTimer=setTimeout(requestFrame,1000);
});
function renderCamera() {
  const camera=latest?.camera||{};
  const captured=Boolean(selectedDetail?.photo);
  const shown=mode==='live'?frameLoaded:captured;
  $('live-image').hidden=mode!=='live'||!frameLoaded;
  $('captured-image').hidden=mode!=='captured'||!captured;
  $('camera-placeholder').hidden=shown;
  if(mode==='live'){
    const fresh=frameLoaded&&Date.now()-lastFrameAt<3000;
    const label=!liveEnabled?'PAUSED':fresh?'LIVE':camera.status==='unavailable'?'UNAVAILABLE':'CONNECTING';
    $('feed-badge').className='feed-badge'+(liveEnabled&&fresh?' live':'');
    $('feed-badge').replaceChildren(node('i'),document.createTextNode(label));
    setText('camera-placeholder-title',!liveEnabled?'Live view paused':camera.status==='unavailable'?'Camera unavailable':'Starting the camera');
    setText('camera-placeholder-detail',camera.error||'The live feed will appear here.');
    setText('frame-size',camera.width?camera.width+' × '+camera.height+' · '+(camera.fps||0)+' fps':'1280 × 960');
    setText('feed-detail',!liveEnabled?'Paused on this device':fresh?'Live camera feed · local network':camera.error||'Waiting for first frame');
  }else{
    $('feed-badge').className='feed-badge';$('feed-badge').replaceChildren(node('i'),document.createTextNode('CAPTURED PHOTO'));
    setText('camera-placeholder-title','No photo selected');setText('camera-placeholder-detail','Capture a photo or select one from your sessions.');
    setText('frame-size','SAVED ON PI');setText('feed-detail',captured?date(selectedDetail.started_at):'Waiting for a capture');
  }
  setText('live-toggle-label',liveEnabled?'Pause live view':'Resume live view');setText('live-toggle-icon',liveEnabled?'Ⅱ':'▷');
}
async function send(command) {
  if(submitting)return;
  submitting=true;$('request-error').hidden=true;$('take-photo').disabled=true;
  try{
    const result=await api('/api/commands',{method:'POST',body:JSON.stringify({command,speak:$('speak').checked})});
    selected=result.id;followLatest=true;detailSignature='';
    if(/photo|picture|scene|see/i.test(command))pendingPhoto=result.id;
    await refresh();
  }catch(error){report(error);}finally{submitting=false;$('take-photo').disabled=false;}
}
function clearResult(){
  selectedDetail=null;photoId=null;detailSignature='';setText('selected-time','Your captured scene and response');
  setText('answer','Capture a photo to get a description of the scene.');setText('answer-status','READY TO CAPTURE');
  $('answer-status').className='answer-status';$('result-photo-button').hidden=true;$('delete-session').hidden=true;
  $('conversation').replaceChildren();$('session-steps').replaceChildren();setText('message-count','0 messages');
  renderCamera();
}
function renderDetail(run) {
  selectedDetail=run;
  const signature=JSON.stringify(run);if(signature===detailSignature)return;detailSignature=signature;
  const end=run.finished_at?(' · '+duration(elapsed(run.started_at,run.finished_at))):'';
  setText('selected-time',date(run.started_at)+end+' · '+run.source);
  $('result-photo-button').hidden=!run.photo;
  if(run.photo&&photoId!==run.id){
    $('result-photo').src=photoUrl(run.id);$('captured-image').src=photoUrl(run.id);photoId=run.id;
  }
  if(!run.photo){$('captured-image').removeAttribute('src');photoId=null;}
  const speaking=run.status==='speaking';
  const messages=run.events.filter(e=>['user','assistant','audio_error','error'].includes(e.kind));
  const replies=messages.filter(e=>e.kind==='assistant');
  const answer=run.response||(speaking?replies.at(-1)?.text:null);
  if(run.error){
    setText('answer',run.error);setText('answer-status','NEEDS ATTENTION');$('answer-status').className='answer-status error';
  }else if(answer){
    setText('answer',answer);setText('answer-status',speaking?'RESPONSE READY':'DESCRIPTION');$('answer-status').className='answer-status';
  }else if(busyStates.has(run.status)){
    const pending={queued:'Your command is queued.',running:'Processing your command…',capturing:'Taking a photo…',analyzing:'Analysing the captured image…',speaking:'Preparing the response…'};
    setText('answer',pending[run.status]||'Processing…');setText('answer-status',stageLabels[run.status].toUpperCase());$('answer-status').className='answer-status busy';
  }else{
    setText('answer',replies.at(-1)?.text||'This session has no description.');setText('answer-status',run.status.toUpperCase());$('answer-status').className='answer-status';
  }
  const conversation=$('conversation');conversation.replaceChildren();
  for(const event of messages){
    const role=event.kind==='user'?'user':event.kind==='assistant'?'assistant':'warning';
    const row=node('div','message '+role);row.append(node('div','avatar',role==='user'?'YOU':role==='assistant'?'SV':'!'));
    const content=node('div');content.append(node('p','message-name',role==='user'?'You · '+run.source:role==='assistant'?'SceneVoice':'Device notice'),node('p','message-text',event.text));row.append(content);conversation.append(row);
  }
  setText('message-count',messages.length+' messages');
  const steps=$('session-steps');steps.replaceChildren();
  for(const event of run.events.filter(e=>e.kind==='stage')){const item=node('li');item.append(node('time',null,date(event.created_at,true)),document.createTextNode(event.text));steps.append(item);}
  $('delete-session').hidden=busyStates.has(run.status);$('delete-session').dataset.id=run.id;
  if(pendingPhoto===run.id&&run.photo){pendingPhoto=null;setMode('captured');}
  renderCamera();
}
async function loadDetail(id) {
  try{return await api('/api/sessions/'+id);}catch(error){if(error.status===404)return null;throw error;}
}
function renderFlow(run) {
  flowDetail=run;
  const list=$('pipeline');list.replaceChildren();
  const titles=['Receive command','Capture photo','Analyse image','Deliver response'];
  const stageOrder={queued:0,running:0,capturing:1,analyzing:2,speaking:3,done:4};
  const state=run?.status||'idle';let current=stageOrder[state]??0;
  const photo=Boolean(run?.photo)||/photo|picture|scene|see/.test(run?.command||'');
  const delivery=run?.events?.find(e=>e.kind==='delivery')?.text;
  const stages=run?.events?.filter(e=>e.kind==='stage')||[];
  const times=[run?.started_at,stages.find(e=>e.text.includes('Taking a photograph'))?.created_at,stages.find(e=>e.text.includes('asking Groq'))?.created_at,stages.find(e=>e.text.includes('Description ready'))?.created_at,run?.finished_at];
  if(state==='failed'||state==='interrupted')current=times[3]?3:times[2]?2:times[1]?1:0;
  for(let i=0;i<4;i++){
    let type=!run?'waiting':state==='failed'||state==='interrupted'?(i<current?'done':i===current?'failed':'waiting'):i<current?'done':i===current?'active':'waiting';
    if(run&&!photo&&(i===1||i===2))type='skipped';
    if(run?.finished_at&&state!=='failed'&&state!=='interrupted'&&type!=='skipped')type='done';
    const item=node('li',type);const marker=node('span','step-marker');
    if(type==='done')marker.append(icon('check-icon'));else marker.append(node('span',null,type==='skipped'?'−':type==='failed'?'!':String(i+1)));
    const body=node('div');body.append(node('strong',null,titles[i]));
    let description=type==='skipped'?'Not needed':type==='active'?'In progress':type==='done'?'Completed':type==='failed'?'Stopped':'Waiting';
    if(i===3&&delivery==='Text only')description=type==='done'?'Displayed in the app':type==='active'?'Displaying answer':description;
    if(times[i]&&times[i+1]&&type==='done')description+=' · '+duration(elapsed(times[i],times[i+1]));
    body.append(node('span','step-detail',description));item.append(marker,body);list.append(item);
  }
  setText('flow-state',run?stageLabels[state]||state:'Idle');
  $('flow-state').className='status-label'+(run?(busyStates.has(state)?' active':state==='done'?' done':' failed'):'');
  setText('flow-context',run?'Session '+run.id.slice(0,8)+' · '+run.source:'Waiting for a command');
  setText('flow-elapsed',run?duration(elapsed(run.started_at,run.finished_at)):'—');
}
function allRuns(){const map=new Map();for(const item of [...(latest?.runs||[]),...older])if(!map.has(item.id))map.set(item.id,item);return [...map.values()];}
function chooseSession(id){
  selected=id;followLatest=false;detailSignature='';setView('workspace');setMode('captured');
  loadDetail(id).then(run=>{if(run)renderDetail(run);else clearResult();}).catch(report);
}
function renderHistory(runs) {
  const search=$('session-search').value.trim().toLowerCase();
  const filtered=runs.filter(r=>(r.command+' '+(r.response||'')).toLowerCase().includes(search));
  const signature=JSON.stringify(filtered.map(r=>[r.id,r.status,r.photo,r.response]))+search;
  if(signature===historySignature)return;historySignature=signature;
  const list=$('history');list.replaceChildren();
  if(!filtered.length){list.append(node('p','empty-note',search?'No matching loaded sessions.':'No sessions yet. Capture your first photo.'));return;}
  for(const run of filtered){
    const button=node('button','history-row');button.type='button';
    if(run.photo){const image=node('img','history-thumb');image.src=photoUrl(run.id);image.alt='';image.loading='lazy';button.append(image);}else button.append(node('span','history-thumb','↗'));
    const info=node('span');info.append(node('span','history-command',run.command),node('span','history-response',run.response||run.error||stageLabels[run.status]||run.status));
    button.append(info,node('span','history-date',date(run.started_at)),node('span','history-source',run.source),node('span','history-status '+run.status,stageLabels[run.status]||run.status));
    button.addEventListener('click',()=>chooseSession(run.id));list.append(button);
  }
}
function renderRecent(runs) {
  const photos=runs.filter(r=>r.photo).slice(0,4);
  const signature=JSON.stringify(photos.map(r=>[r.id,r.response,r.status]));if(signature===recentSignature)return;recentSignature=signature;
  const list=$('recent-captures');list.replaceChildren();
  if(!photos.length){list.append(node('p','empty-note','Your first capture will appear here.'));return;}
  for(const run of photos){
    const button=node('button','capture-tile');button.type='button';const image=node('img');image.src=photoUrl(run.id);image.alt='';image.loading='lazy';
    const body=node('div','capture-tile-body');body.append(node('span','capture-tile-title',run.response||run.error||'Processing photo…'),node('span','capture-tile-time',date(run.started_at)+' · '+(stageLabels[run.status]||run.status)));button.append(image,body);
    button.addEventListener('click',()=>chooseSession(run.id));list.append(button);
  }
}
function renderActivity(events){
  const filtered=events.filter(e=>['stage','system','error','audio_error','audio_started','audio_finished'].includes(e.kind)).slice(0,16);
  const signature=JSON.stringify(filtered.map(e=>e.id));if(signature===activitySignature)return;activitySignature=signature;
  const list=$('activity');list.replaceChildren();
  for(const event of filtered){const row=node('div','activity-item '+event.kind);row.append(node('span','activity-dot'));const body=node('div','activity-content');body.append(node('p','activity-text',event.text),node('time','activity-time',date(event.created_at,true)));row.append(body);list.append(row);}
  if(!filtered.length)list.append(node('p','empty-note','Activity appears as commands are processed.'));
}
function renderDevice(data){
  const d=data.device||{},camera=data.camera||{};
  setText('network-url',location.origin+'/');$('network-url').href=location.origin+'/';
  setText('sidebar-host',d.hostname||'Raspberry Pi');setText('sidebar-ip',location.host);
  setText('camera-status',camera.status==='live'?'Live · '+camera.fps+' fps':camera.status==='starting'?'Starting':camera.status==='standby'?'Standby':'Unavailable');
  setText('mic-status',data.runtime.stage==='listening'?'Listening':data.runtime.stage==='audio_unavailable'?'Not connected':data.runtime.stage==='loading_model'?'Loading model':'Standby');
  setText('output-status',data.audio.output==='auto'?'Headphones / auto':data.audio.output);
  setText('memory-status',d.memory_available_mb!==null&&d.memory_available_mb!==undefined?d.memory_available_mb+' MB':'—');
  setText('runtime-detail',data.runtime.detail);
  const rows=[['Model',d.model||'Raspberry Pi'],['Hostname',d.hostname||'—'],['Address',location.host],['Service uptime',Math.floor(data.uptime_seconds/60)+' min'],['Camera',camera.status==='live'?camera.width+' × '+camera.height+' · '+camera.fps+' fps':camera.status],['Voice input',data.audio.input==='auto'?'Auto detect':data.audio.input],['Audio output',data.audio.output==='auto'?'Headphones / auto':data.audio.output],['Memory available',d.memory_available_mb!==undefined?d.memory_available_mb+' MB':'—'],['Temperature',d.temperature_c!==null&&d.temperature_c!==undefined?d.temperature_c+' °C':'—'],['Storage available',d.disk_free_gb!==undefined?d.disk_free_gb+' GB':'—']];
  const details=$('device-details');details.replaceChildren();
  for(const [key,value]of rows){const row=node('div');row.append(node('dt',null,key),node('dd',null,value));details.append(row);}
  const list=$('processes');list.replaceChildren();
  for(const process of d.processes||[]){const row=node('div','process-item');row.append(node('span',null,process.name),node('small',null,'PID '+process.pid));list.append(row);}
}
async function refresh(){
  if(syncing)return;syncing=true;
  try{
    const data=await api('/api/state');latest=data;
    $('connection').className='connection-status online';$('connection').replaceChildren(node('i'),document.createTextNode('Connected to Pi'));
    setText('health-label','Online');$('sidebar-led').style.background='';
    const total=Object.values(data.counts).reduce((a,b)=>a+b,0);setText('nav-count',total);setText('history-total',total);
    setText('queue-depth',data.queue_depth?data.queue_depth+' command'+(data.queue_depth===1?'':'s')+' queued':'No commands queued');
    setText('retention','Up to '+data.retention.sessions+' sessions · '+Math.round(data.retention.photo_bytes/1e6)+' MB of photos · saved on the Pi');
    if(!olderLoaded)cursor=data.next_cursor;$('load-more').hidden=!cursor;
    if(followLatest&&data.runs.length)selected=data.runs[0].id;
    const flowId=data.active?.id||data.runs[0]?.id;
    const detailId=selected;
    const [detail,flow]=await Promise.all([detailId?loadDetail(detailId):Promise.resolve(null),flowId&&flowId!==detailId?loadDetail(flowId):Promise.resolve(null)]);
    if(selected===detailId){if(detail)renderDetail(detail);else{selected=null;clearResult();}}
    renderFlow(flowId===detailId?detail:flow);renderDevice(data);
    renderHistory(allRuns());renderRecent(data.runs);renderActivity(data.events);renderCamera();
  }catch(error){
    $('connection').className='connection-status';$('connection').replaceChildren(node('i'),document.createTextNode('Reconnecting'));
    setText('health-label','Offline');setText('runtime-detail','Cannot reach the Pi. Checking again shortly.');
  }finally{syncing=false;}
}
function showPreview(){
  const captured=selectedDetail?.photo&&(mode==='captured'||this?.id==='result-photo-button');
  if(captured){
    $('dialog-photo').src=photoUrl(selectedDetail.id);setText('dialog-title','Captured photo · '+date(selectedDetail.started_at));
    setText('dialog-description',selectedDetail.response||'Saved camera photograph.');$('download-photo').href=photoUrl(selectedDetail.id);$('download-photo').hidden=false;
  }else if(frameLoaded){
    $('dialog-photo').src=$('live-image').src;setText('dialog-title','Camera frame preview');
    setText('dialog-description','Use Capture & describe to save a photo and get a scene description.');$('download-photo').hidden=true;
  }else return;
  $('photo-dialog').showModal();
}
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>setView(button.dataset.view)));
$('take-photo').addEventListener('click',()=>send('click photo'));
$('repeat').addEventListener('click',()=>send('repeat'));
$('view-live').addEventListener('click',()=>setMode('live'));
$('view-captured').addEventListener('click',()=>setMode('captured'));
$('toggle-live').addEventListener('click',()=>{liveEnabled=!liveEnabled;clearTimeout(liveTimer);if(liveEnabled)setMode('live');else renderCamera();});
$('expand-photo').addEventListener('click',showPreview);$('result-photo-button').addEventListener('click',showPreview);
$('close-preview').addEventListener('click',()=>$('photo-dialog').close());
$('photo-dialog').addEventListener('click',event=>{if(event.target===$('photo-dialog'))$('photo-dialog').close();});
$('refresh').addEventListener('click',()=>refresh());
$('view-all-sessions').addEventListener('click',()=>setView('sessions'));
$('session-search').addEventListener('input',()=>{historySignature='';renderHistory(allRuns());});
$('command-form').addEventListener('submit',event=>{event.preventDefault();const command=$('command-input').value.trim();if(command){$('command-input').value='';send(command);}});
$('load-more').addEventListener('click',async()=>{if(!cursor)return;try{const page=await api('/api/state?before='+cursor);older.push(...page.runs);olderLoaded=true;cursor=page.next_cursor;historySignature='';renderHistory(allRuns());$('load-more').hidden=!cursor;}catch(error){report(error);}});
$('delete-session').addEventListener('click',async()=>{const id=$('delete-session').dataset.id;if(!id||!confirm('Delete this session, its photo and its conversation from the Pi?'))return;try{await api('/api/sessions/'+id+'/delete',{method:'POST',body:'{}'});older=older.filter(r=>r.id!==id);selected=null;followLatest=true;detailSignature='';await refresh();}catch(error){report(error);}});
document.addEventListener('visibilitychange',()=>{clearTimeout(liveTimer);if(!document.hidden){refresh();requestFrame();}});
window.addEventListener('pagehide',()=>{liveEnabled=false;clearTimeout(liveTimer);});
setInterval(()=>{if(!document.hidden)refresh();},1000);
setInterval(()=>{setText('clock',new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}));if(flowDetail&&!flowDetail.finished_at)setText('flow-elapsed',duration(elapsed(flowDetail.started_at)));},1000);
renderFlow(null);refresh();requestFrame();
