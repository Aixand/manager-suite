const $ = (id) => document.getElementById(id);
const state = {id: null, chats: [], agents: [], events: new Map(), cursor: 0, connected: false,
  queue:null, activeTurn:null, queueSignature:'', busy: false, polling: false, requests: '', commands: [], catalog: null, config: null,
  results: {}, tab: 'activity', sending: false, error: null};
let pendingSend=null, pendingStart=null, pendingSetting=null;
state.recovery=null;state.inspectedQueueId=null;state.availability=null;
state.configuring=false;
function operationId() {
  const bytes=crypto.getRandomValues(new Uint8Array(16));
  bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const hex=Array.from(bytes,value=>value.toString(16).padStart(2,'0')).join('');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
const node = (tag, cls, text) => {const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;};
const current = () => state.chats.find(chat => chat.id === state.id);
function toast(message) { $('toast').textContent=message; $('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,7000); }
async function api(path, body) {
  const response=await fetch(path, body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const value=await response.json();
  if(!response.ok || value.error){const error=new Error(value.error?.message || 'Request failed');error.detail=value.error;throw error;}
  return value;
}
function receipt(value) {if(value.status && !['accepted','completed'].includes(value.status))throw new Error(value.error?.message || `Operation ${value.status}`);return value;}
async function chats() {state.chats=(await api('/api/chats')).chats;renderSidebar();}
function renderSidebar() {
  const list=$('chat-list');list.replaceChildren();$('chat-count').textContent=state.chats.length;
  for(const chat of [...state.chats].reverse()) {const button=node('button','chat-item'+(chat.id===state.id?' active':''));button.append(node('span','name',chat.title),node('small','',chat.provider==='demo'?'Offline SDK demo':chat.agent));button.onclick=()=>select(chat.id);list.append(button);}
  if(!state.chats.length)list.append(node('p','muted','Your conversations will appear here.'));
}
function controls() {
  const chat=current();
  $('message').disabled=!state.id || !state.connected || state.sending;
  $('message').placeholder=!state.id?'Start a conversation to send a message…':!state.connected?'Reconnect to continue this conversation…':'Message your agent, or type / for commands…';
  $('send').disabled=!state.connected || (state.busy && !state.queue?.modes?.after_turn?.supported) || state.sending || state.configuring || !$('message').value.trim();
  $('send').setAttribute('aria-label',state.busy?'Queue message':'Send message');
  $('send').title=state.busy?'Queue after the current turn':'Send message';
  $('queue-delivery').hidden=!state.busy;
  const toolMode=state.queue?.modes?.next_tool_completion;
  const toolOption=$('delivery-mode').querySelector('[value=next_tool_completion]');
  toolOption.disabled=!toolMode?.supported || !state.queue?.running_tools?.length;
  $('queue-hint').textContent=!toolMode?.supported?(toolMode?.reason || 'Start a new conversation to enable tool-completion guidance.') : $('delivery-mode').value==='next_tool_completion'?'Waits for all currently running tools, then sends guidance into this turn. The agent may already have continued; if the turn ends, the message stays queued.':state.queue?.running_tools?.length?'Tool-completion guidance is also available.':'Tool-completion guidance becomes available while a tool is running.';
  $('delivery-mode').disabled=!state.queue?.modes?.after_turn?.supported;
  if($('delivery-mode').value==='next_tool_completion' && toolOption.disabled)$('send').disabled=true;
  $('cancel').hidden=!state.busy || !state.connected;
  $('commands').disabled=!state.connected;
  document.querySelectorAll('.settings-field select').forEach(el=>el.disabled=!state.connected || state.busy || state.configuring);
  $('detach').disabled=!state.id || state.availability==='stopped';
  $('detach').textContent=state.connected?'Detach':'Reconnect';
  $('stop').disabled=!state.id || state.availability==='stopped';
  $('stop').title='Stops all conversations sharing this backend. Closing the browser only detaches.';
  $('demo-shortcuts').hidden=!chat || chat.provider!=='demo';
  $('title').textContent=chat?.title || 'A place to test the conversation.';
  $('agent-label').textContent=chat?.agent.toUpperCase() || 'CONVERSATION MANAGER';
  $('mode').textContent=chat?(chat.provider==='demo'?'OFFLINE DEMO':'LIVE PROVIDER'):'No conversation';
  $('mode').className='mode-badge'+(chat?(chat.provider==='demo'?' demo':' live'):'');
  $('connection-status').textContent=state.error || (!chat?'Choose an agent to begin':!state.connected?(state.availability==='stopped'?'Backend stopped':'Frontend detached'):state.busy?'Agent is working':'Connected · ready');
  $('status-dot').className='status-dot'+(state.connected?(state.busy?' busy':' connected'):'');
  $('footer-mode').textContent=chat?.provider==='demo'?'Offline SDK fixture · no provider quota':chat?'Live provider · uses configured account quota':'Offline demo available · no provider quota needed';
}
async function select(id) {
  state.id=id;state.recovery=null;state.inspectedQueueId=null;state.availability=null;state.queue=null;state.activeTurn=null;state.queueSignature='';$('queue-panel').hidden=true;state.cursor=0;state.events.clear();state.results={};state.requests='';state.connected=false;state.busy=false;state.error=null;state.commands=[];state.config=null;$('requests').replaceChildren();$('palette').hidden=true;
  renderSidebar();controls();$('sidebar').classList.remove('open');
  try {
    const history=await api('/api/history?id='+encodeURIComponent(id));
    if(state.id!==id)return;
    for(const event of history.events)state.events.set(event.sequence,event);
    Object.assign(state.results,history.results || {});state.queue=history.queue || null;renderQueue();
    renderMessages();await sync();
    if(state.connected)await loadCatalog();
  } catch(error) {state.error=error.message;controls();toast(error.message);}
}
async function sync() {
  if(!state.id || state.polling || state.error)return;
  state.polling=true;const id=state.id;
  try {
    const value=await api(`/api/sync?id=${encodeURIComponent(id)}&after=${state.cursor}`);
    if(id!==state.id)return;
    const previousBusy=state.busy, previousConnected=state.connected;
    state.connected=value.connected && !value.state?.closed;state.availability=value.availability || (value.state?.closed?'unavailable':state.connected?'attached':'detached');state.busy=Boolean(value.state?.active_turn);state.activeTurn=value.state?.active_turn;if(value.state)state.queue=value.state.queue || null;renderQueue();state.error=value.state?.closed?'Backend connection closed. Inspect Recovery.':null;
    if(value.cursor!==undefined)state.cursor=value.cursor;
    const index=state.chats.findIndex(chat=>chat.id===id);if(index>=0)state.chats[index]=value.chat;
    for(const event of value.events)state.events.set(event.sequence,event);
    Object.assign(state.results,value.results || {});
    renderRequests(value.state?.requests || []);renderMessages();renderSidebar();controls();if(previousBusy!==state.busy)renderPalette();
    if(state.connected && ((!previousConnected)||(previousBusy && !state.busy)))loadCatalog().catch(error=>toast(error.message));
    if(!$('inspector').hidden && state.tab==='activity')renderInspector();
  } catch(error) {if(id===state.id){state.error=error.message.includes('Recorded worker unavailable')?'Backend unavailable. Inspect Recovery or reconnect.':error.message;state.connected=false;state.busy=false;controls();renderQueue();}}
  finally {state.polling=false;}
}
function renderMessages() {
  const chat=current();if(!chat)return;
  const timeline=$('timeline'), nearBottom=timeline.scrollHeight-timeline.scrollTop-timeline.clientHeight<140;
  const turns=new Map();
  const get=(id)=>{if(!turns.has(id))turns.set(id,{id,text:'',tools:[],guidance:[],status:'',error:null});return turns.get(id);};
  for(const message of chat.messages || [])get(message.turn_id || message.operation_id).user=message.text;
  for(const event of [...state.events.values()].sort((a,b)=>a.sequence-b.sequence)) {
    if(!event.turn_id)continue;
    const turn=get(event.turn_id),payload=event.payload || {};if(turn.firstSequence===undefined)turn.firstSequence=event.sequence;
    if(event.kind==='message.input' && payload.delivery==='after_turn')turn.user=payload.text;
    if(event.kind==='message.input' && payload.delivery==='steer')turn.guidance.push(payload.text);
    if(event.kind==='message.delta' && payload.role!=='reasoning')turn.text+=payload.text || '';
    if(event.kind.startsWith('tool.'))turn.tools.push(event.kind.replace('tool.','')+(payload.content?.name?' · '+payload.content.name:''));
    if(event.kind==='turn.completed'){turn.status=payload.status;turn.error=payload.error;}
    if(event.kind==='error'){turn.error={message:payload.reason};turn.status='uncertain';}
  }
  for(const [id,result] of Object.entries(state.results)){const turn=get(id);if(result && typeof result.final_text==='string')turn.text=result.final_text;if(result){turn.status=result.status;turn.error=result.error;}}
  timeline.replaceChildren();
  if(!turns.size){const welcome=node('div','empty');welcome.append(node('div','empty-mark','↗'),node('h2','',chat.provider==='demo'?'Your SDK worker is ready.':'Your agent is connected.'),node('p','',chat.provider==='demo'?'Send a message or try one of the demo scenarios below.':'Send your first message to begin.'));timeline.append(welcome);return;}
  for(const turn of [...turns.values()].sort((a,b)=>(a.firstSequence ?? Infinity)-(b.firstSequence ?? Infinity))) {
    const container=node('div','turn');
    if(turn.user){const user=node('article','message user'),body=node('div','message-body');body.append(node('div','message-top','You'),node('div','message-text',turn.user));user.append(node('div','avatar','Y'),body);container.append(user);}
    for(const text of turn.guidance){const guidance=node('article','message guidance'),body=node('div','message-body');body.append(node('div','message-top','Guidance during this turn'),node('div','message-text',text));guidance.append(node('div','avatar','↗'),body);container.append(guidance);}
    const assistant=node('article','message assistant'),body=node('div','message-body'),heading=node('div','message-top',chat.provider==='demo'?'SDK demo':chat.agent.split(' · ')[0]);
    if(turn.status)heading.append(node('span','turn-status '+turn.status,turn.status));
    body.append(heading);
    if(turn.text)body.append(node('div','message-text',turn.text));
    for(const tool of [...new Set(turn.tools)])body.append(node('div','tool-chip','⌘ '+tool));
    if(turn.error)body.append(node('div','message-text',turn.error.message || JSON.stringify(turn.error)));
    if(!turn.status && state.busy){const typing=node('span','typing');typing.append(node('i'),node('i'),node('i'));body.append(typing);}
    assistant.append(node('div','avatar assistant','↗'),body);container.append(assistant);timeline.append(container);
  }
  if(nearBottom)timeline.scrollTop=timeline.scrollHeight;
}
function renderRequests(requests) {
  const signature=JSON.stringify(requests.map(request=>[request.request_id,request.state]));if(signature===state.requests)return;state.requests=signature;
  const area=$('requests');area.replaceChildren();
  for(const request of requests) {
    const card=node('section','request-card'),params=request.native?.params || {};
    card.append(node('h3','',request.kind==='permission'?'Permission requested':'Your input is needed'));
    card.append(node('p','',params.message || params.question || (request.kind==='permission'?'The agent is waiting for a decision.':'Answer the agent to continue.')));
    const buttons=node('div','buttons');
    const reply=async(response)=>{for(const button of card.querySelectorAll('button'))button.disabled=true;try{receipt(await api('/api/respond',{id:state.id,request_id:request.request_id,response}));toast('Response delivered');state.requests='';await sync();}catch(error){toast(error.message);for(const button of card.querySelectorAll('button'))button.disabled=false;}};
    if(request.choices?.length) {
      for(const choice of request.choices){const button=node('button',choice.kind?.includes('reject')?'reject':'',choice.name);button.onclick=()=>reply({outcome:'selected',option_id:choice.optionId});buttons.append(button);}
    } else if(request.native_method?.endsWith('/requestApproval') && !request.native_method?.includes('permissions/')) {
      for(const [label,decision] of [['Allow once','accept'],['Deny','decline']]){const button=node('button',decision==='decline'?'reject':'',label);button.onclick=()=>reply({native_result:{decision}});buttons.append(button);}
    } else if(params.questions?.length) {
      const answers={};
      for(const question of params.questions){const label=node('label','',question.question),input=node('input');input.setAttribute('aria-label',question.question);label.append(input);card.append(label);answers[question.id]=input;}
      const button=node('button','','Submit answer');button.onclick=()=>{const values={};for(const [key,input]of Object.entries(answers))values[key]=request.native_method==='item/tool/requestUserInput'?{answers:[input.value]}:input.value;reply({native_result:{answers:values}});};buttons.append(button);
    } else {
      const label=node('label','','Response JSON'),input=node('textarea');input.rows=4;input.value='{}';label.append(input);card.append(label);const details=node('details'),summary=node('summary','','View response schema');details.append(summary,node('pre','',JSON.stringify(request.response_schema,null,2)));card.append(details);const button=node('button','','Submit response');button.onclick=()=>{try{reply({native_result:JSON.parse(input.value)});}catch(error){toast(error.message);}};buttons.append(button);
    }
    card.append(buttons);area.append(card);
  }
}
const settingChoices = setting => (setting?.options || []).flatMap(option=>option.options || [option]);
function settingFor(command) {return state.config?.options?.find(option=>option.id===command?.setting_id);}
function typedCommand() {return $('message').value.match(/^\/(\S+)\s([\s\S]*)$/);}
async function loadCatalog() {
  const id=state.id;
  const [catalog,config]=await Promise.all([api('/api/commands?id='+encodeURIComponent(id)),api('/api/settings?id='+encodeURIComponent(id))]);
  if(state.id!==id)return;
  state.catalog=catalog;state.config=config;state.commands=[...catalog.commands];
  for(const setting of config.options || []) {
    if(state.commands.some(command=>command.execution==='setting' && command.setting_id===setting.id))continue;
    const name=({effort:'reasoning',serviceTier:'speed',permission_mode:'permissions'})[setting.id] || setting.id;
    if(state.commands.some(command=>command.name===name && command.available))continue;
    state.commands=state.commands.filter(command=>command.name!==name);
    state.commands.push({name,slash:'/'+name,description:'Change '+(setting.name || name),execution:'setting',setting_id:setting.id,available:true,executable:false,requires_idle:true,source:'session-configuration'});
  }
  const model=config.options?.find(option=>option.id==='model');$('model-label').textContent=model?.currentValue || '';
  renderPalette();if(!$('inspector').hidden)renderInspector();controls();
}
async function applySetting(setting,value,fromComposer=false) {
  if(!state.connected || state.busy || state.configuring){toast('Settings can be changed when the conversation is idle.');return;}
  const id=state.id, composerText=$('message').value, intent=JSON.stringify([id,setting.id,value]);
  if(pendingSetting?.intent!==intent)pendingSetting={intent,id:operationId()};
  state.configuring=true;controls();renderPalette();
  try {
    receipt(await api('/api/configure',{id,name:setting.id,value,operation_id:pendingSetting.id}));pendingSetting=null;
    if(id!==state.id)return;
    if(fromComposer && $('message').value===composerText){$('message').value='';$('palette').hidden=true;}
    await loadCatalog();toast((setting.name || setting.id)+' updated');
  } catch(error){if(error.detail && !error.detail.uncertain)pendingSetting=null;toast(error.message);if(id===state.id)await loadCatalog().catch(()=>{});}
  finally {state.configuring=false;controls();renderPalette();}
}
function renderPalette() {
  const list=$('palette-list');list.replaceChildren();const typed=typedCommand();
  const command=state.commands.find(row=>row.name===typed?.[1]);
  if(typed && command?.execution==='setting') {
    const setting=settingFor(command),query=typed[2].trim().toLowerCase();
    list.append(node('p','muted',(setting?.name || command.name)+(state.busy?' · available after the current turn':' · choose a value')));
    const choices=settingChoices(setting).filter(choice=>String(choice.name || choice.value).toLowerCase().includes(query)||String(choice.value).toLowerCase().includes(query));
    for(const choice of choices){const button=node('button','command-row'),current=choice.value===setting.currentValue,text=node('span','',choice.name || String(choice.value));text.append(node('small','',String(choice.value)));button.append(text,node('span','kind',current?'CURRENT':'APPLY'));button.disabled=!state.connected || state.busy || state.configuring || current;button.onclick=()=>applySetting(setting,choice.value,true);list.append(button);}
    if(!choices.length)list.append(node('p','muted','No matching advertised values.'));
    return;
  }
  const query=$('message').value.startsWith('/')?$('message').value.slice(1).split(/\s/)[0].toLowerCase():'';
  const commands=state.commands.filter(command=>(command.available || $('show-unavailable').checked)&&command.name.toLowerCase().includes(query));
  for(const command of commands){const button=node('button','command-row'),text=node('span','',command.slash);text.append(node('small','',command.available?command.description:command.reason));button.append(text,node('span','kind',command.execution==='setting'?'SETTING':command.executable?'COMMAND':'UNAVAILABLE'));button.disabled=!command.available || (command.requires_idle && state.busy);button.onclick=()=>{$('message').value=command.slash+' ';$('message').focus();controls();renderPalette();$('palette').hidden=command.execution!=='setting';};list.append(button);}
  if(!commands.length)list.append(node('p','muted',state.commands.length?'No matching supported commands.':'No executable commands are advertised yet.'));
}
async function send(event) {
  event.preventDefault();if(!state.connected || state.sending || state.configuring)return;
  const text=$('message').value.trim();if(!text)return;
  let path='/api/send',body={id:state.id,text};
  if(state.busy){if(!state.queue?.modes?.[$('delivery-mode').value]?.supported){toast('Start a new conversation supporting this delivery mode.');return;}if(text.startsWith('/')){toast('Queue plain messages; run slash commands when the conversation is idle.');return;}path='/api/queue';body={id:state.id,action:'enqueue',text,mode:$('delivery-mode').value,expected_turn_id:state.activeTurn.turn_id};}
  if(text.startsWith('/')){const match=text.match(/^\/(\S+)(?:\s+([\s\S]*))?$/);const command=state.commands.find(command=>command.name===match?.[1]);if(command?.execution==='setting'){const setting=settingFor(command),argument=match[2]?.trim();const choice=settingChoices(setting).find(option=>String(option.value)===argument || String(option.name).toLowerCase()===argument?.toLowerCase());if(choice){await applySetting(setting,choice.value,true);}else{renderPalette();$('palette').hidden=false;toast('Choose an advertised setting value.');}return;}if(!command?.executable){toast('Choose an executable command from the command list.');return;}path='/api/command';body={id:state.id,name:command.name,arguments:match[2] || ''};}
  const intent=JSON.stringify([path,body]);if(pendingSend?.intent!==intent)pendingSend={intent,id:operationId()};body.operation_id=pendingSend.id;
  state.sending=true;controls();
  try {receipt(await api(path,body));pendingSend=null;if($('message').value.trim()===text)$('message').value='';$('palette').hidden=true;await chats();await sync();}
  catch(error){if(error.detail && !error.detail.uncertain)pendingSend=null;toast(error.message);}finally{state.sending=false;controls();}
}
async function openDialog() {
  $('start-error').textContent='';$('start-submit').disabled=false;$('start-submit').textContent='Create conversation ↗';
  pendingStart=null;
  try {const data=await api('/api/agents');state.agents=data.agents;$('agent').replaceChildren();for(const agent of data.agents){const option=node('option','',agent.label);option.value=agent.key;option.disabled=!agent.supported;$('agent').append(option);}$('cwd').value=data.demo_workspace;if(data.discovery_error)toast('Live discovery: '+data.discovery_error);agentChanged();$('start-dialog').showModal();}catch(error){toast(error.message);}
}
function agentChanged() {const demo=$('agent').value==='demo';$('model').disabled=demo;$('cwd').disabled=demo;$('mode-explanation').textContent=demo?'Offline SDK fixture. No credentials, inference, or account quota.':'Live agent. Sending messages or commands uses your configured provider quota.';}
async function start(event) {event.preventDefault();$('start-submit').disabled=true;$('start-submit').textContent='Connecting…';$('start-error').textContent='';const body={agent:$('agent').value,cwd:$('cwd').value,model:$('model').value};const intent=JSON.stringify(body);if(pendingStart?.intent!==intent)pendingStart={intent,id:operationId()};body.start_id=pendingStart.id;try{const value=await api('/api/start',body);pendingStart=null;$('start-dialog').close();await chats();await select(value.id);}catch(error){$('start-error').textContent=error.message;}finally{$('start-submit').disabled=false;$('start-submit').textContent='Create conversation ↗';}}
async function openInspector(tab=state.tab) {state.tab=tab;$('inspector').hidden=false;document.querySelectorAll('[data-tab]').forEach(button=>button.classList.toggle('active',button.dataset.tab===tab));if(tab==='settings' && state.id && state.connected){try{await loadCatalog();}catch(error){toast(error.message);}}if(tab==='recovery' && state.id){await loadRecovery();}renderInspector();}
function renderInspector() {
  const area=$('inspector-body');area.replaceChildren();
  if(!state.id){area.append(node('p','','Start a conversation to inspect its activity and controls.'));return;}
  if(state.tab==='activity') {const events=[...state.events.values()].sort((a,b)=>b.sequence-a.sequence);area.append(node('div','inspector-summary',`${events.length} observed events · ${state.connected?'attached':'detached'}`));for(const event of events.slice(0,120)){const row=node('div','event-row');row.append(node('b','',event.kind),node('time','',event.observed_at?new Date(event.observed_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}):''));area.append(row);}if(!events.length)area.append(node('p','','No events yet.'));}
  if(state.tab==='catalog'){area.append(node('div','inspector-summary','Only supported commands can be invoked. Settings controls open the settings panel.'));for(const command of state.commands){const row=node('div','catalog-entry');row.append(node('b','',command.slash),node('small',command.available?'supported':'',command.available?command.execution:command.reason),node('small','',command.description));area.append(row);}if(!state.commands.length)area.append(node('p','','No registered commands were returned.'));}
  if(state.tab==='recovery'){renderRecovery(area);}
  if(state.tab==='settings'){if(!state.config){area.append(node('p','','Connect to load session settings.'));return;}for(const setting of state.config.options || []){const label=node('label','settings-field');label.append(node('span','',setting.name || setting.id));const select=node('select');select.setAttribute('aria-label',setting.name || setting.id);for(const option of setting.options || []){for(const item of option.options || [option]){const el=node('option','',item.name || item.value);el.value=item.value;select.append(el);}}select.value=setting.currentValue;select.disabled=!state.connected || state.busy || state.configuring;select.onchange=()=>applySetting(setting,select.value);label.append(select);area.append(label);}area.append(node('p','muted','Settings come from the connected session. Some controls require an idle turn.'));}
}
$('new-chat').onclick=openDialog;$('empty-start').onclick=openDialog;$('start-form').onsubmit=start;$('agent').onchange=agentChanged;$('dialog-close').onclick=()=>$('start-dialog').close();$('composer').onsubmit=send;
$('message').addEventListener('input',()=>{controls();if($('message').value.startsWith('/')){renderPalette();$('palette').hidden=false;}else $('palette').hidden=true;});
$('message').addEventListener('keydown',event=>{if(event.key==='ArrowDown'&&!$('palette').hidden){const first=$('palette-list').querySelector('button:not(:disabled)');if(first){event.preventDefault();first.focus();}return;}if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();$('composer').requestSubmit();}if(event.key==='Escape')$('palette').hidden=true;});
$('commands').onclick=()=>{renderPalette();$('palette').hidden=!$('palette').hidden;};$('show-unavailable').onchange=renderPalette;
$('detach').onclick=async()=>{try{await api(state.connected?'/api/detach':'/api/reconnect',{id:state.id});state.error=null;await sync();if(state.connected)await loadCatalog();}catch(error){toast(error.message);}};
$('stop').onclick=async()=>{try{receipt(await api('/api/stop',{id:state.id}));toast('Backend stopped. Other chats sharing this backend also stop.');await sync();}catch(error){toast(error.message);}};
$('cancel').onclick=async()=>{try{receipt(await api('/api/cancel',{id:state.id}));toast('Cancellation requested; waiting for the agent.');await sync();}catch(error){toast(error.message);}};
$('inspector-toggle').onclick=()=>{$('inspector').hidden?openInspector():$('inspector').hidden=true;};$('inspector-close').onclick=()=>$('inspector').hidden=true;document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>openInspector(button.dataset.tab));
document.querySelectorAll('[data-prompt]').forEach(button=>button.onclick=()=>{$('message').value=button.dataset.prompt;$('message').dispatchEvent(new Event('input'));$('message').focus();});
$('menu').onclick=()=>$('sidebar').classList.toggle('open');
document.addEventListener('keydown',event=>{if(event.key==='n'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!$('start-dialog').open)openDialog();});
setInterval(sync,700);chats().then(()=>{if(state.chats.length)select(state.chats[state.chats.length-1].id);}).catch(error=>toast(error.message));

$('palette-list').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();$('palette').hidden=true;$('message').focus();return;}if(!['ArrowDown','ArrowUp'].includes(event.key))return;const buttons=[...$('palette-list').querySelectorAll('button:not(:disabled)')];const index=buttons.indexOf(document.activeElement);if(index<0)return;event.preventDefault();const next=index+(event.key==='ArrowDown'?1:-1);if(next<0){$('message').focus();}else{buttons[next%buttons.length]?.focus();}});

async function queueAction(action,extra={},operation=operationId()) {
  try{receipt(await api('/api/queue',{id:state.id,action,...extra,operation_id:operation}));await sync();}
  catch(error){toast(error.message);}
}
function renderQueue() {
  const panel=$('queue-panel'),queue=state.queue;
  const signature=JSON.stringify([queue,state.connected]);if(signature===state.queueSignature)return;state.queueSignature=signature;
  panel.replaceChildren();const items=(queue?.items || []).filter(item=>!['delivered','removed'].includes(item.status));panel.hidden=!items.length;
  if(!items.length)return;
  panel.append(node('p','',`${items.length} queued message${items.length===1?'':'s'} · ${queue.paused?'paused':'waiting'}`));
  if(queue.paused)panel.append(node('p','',queue.reason));
  const toggle=node('button','',queue.paused?'Resume queue':'Pause queue'),toggleOp=operationId();toggle.disabled=!state.connected;toggle.onclick=()=>queueAction(queue.paused?'resume':'pause',{},toggleOp);panel.append(toggle);
  for(const item of items){const row=node('div','queue-item');row.append(node('p','',item.text),node('small','',item.status==='queued'?(item.blocked_reason || (item.mode==='next_tool_completion'?'At next tool completion · waiting':'After turn · waiting')):item.status+' · inspect delivery; no automatic retry'));const inspect=node('button','','Inspect delivery');inspect.onclick=()=>{state.inspectedQueueId=item.queue_id;openInspector('recovery');};row.append(inspect);if(item.status==='queued'){
    const remove=node('button','','Remove'),removeOp=operationId();remove.disabled=!state.connected;remove.onclick=()=>queueAction('remove',{queue_id:item.queue_id},removeOp);
    const edit=node('button','','Edit');edit.disabled=!state.connected;edit.onclick=()=>{edit.disabled=true;const field=node('textarea');field.value=item.text;field.setAttribute('aria-label','Edit queued message');const save=node('button','','Save queued message');let editIntent=null;save.onclick=()=>{if(editIntent?.text!==field.value)editIntent={text:field.value,id:operationId()};queueAction('edit',{queue_id:item.queue_id,text:field.value},editIntent.id);};row.append(field,save);field.focus();};row.append(edit,remove);
  }panel.append(row);}
}

async function loadRecovery() {
  const id=state.id,queueId=state.inspectedQueueId;
  try {const value=await api('/api/recovery?id='+encodeURIComponent(id)+(queueId?'&queue_id='+encodeURIComponent(queueId):''));if(state.id===id && state.inspectedQueueId===queueId)state.recovery=value;}
  catch(error){toast(error.message);}
}
function renderRecovery(area) {
  const value=state.recovery;
  if(!value){area.append(node('p','','Recovery information is unavailable.'));return;}
  area.append(node('p','',state.connected?'Attached to the recorded worker.':'Detached or unavailable. Recorded history and receipts remain readable.'));
  area.append(node('p','',`Startup receipt: ${value.startup.status}. This records startup, not current worker availability.`));
  const refresh=node('button','quiet','Refresh recovery');refresh.onclick=()=>openInspector('recovery');area.append(refresh);
  const reconnect=node('button','quiet','Reconnect recorded session');reconnect.onclick=()=>$('detach').click();reconnect.disabled=state.connected || state.availability==='stopped';area.append(reconnect);
  const fresh=node('button','quiet','New conversation');fresh.onclick=openDialog;area.append(fresh);
  for(const item of value.queue.items || []){const button=node('button','quiet',item.text.slice(0,60)+' · '+item.status);button.onclick=()=>{state.inspectedQueueId=item.queue_id;openInspector('recovery');};area.append(button);}
  if(value.delivery){const evidence=value.delivery;const outcome={not_dispatched:'Not sent',handed_off:'Guidance handed off',accepted:'Accepted by worker',completed:'Completed',failed:'Failed',cancelled:'Cancelled',unconfirmed:'Delivery unconfirmed',intent_conflict:'Receipt does not match message'}[evidence.outcome] || evidence.outcome;area.append(node('h3','','Delivery: '+outcome));area.append(node('p','',evidence.can_reconcile?'A matching receipt confirms delivery outcome. Reconcile updates the queue without resending; remaining work stays paused.':'No automatic resend. Inspect the receipt and turn outcome before deciding on further work.'));
    if(evidence.can_reconcile){const button=node('button','quiet','Reconcile confirmed delivery'),operation=operationId();button.disabled=!state.connected;button.onclick=async()=>{await queueAction('reconcile',{queue_id:evidence.item.queue_id},operation);await openInspector('recovery');};area.append(button);}
    const details=node('details'),summary=node('summary','','Retained receipt and turn result');details.append(summary,node('pre','',JSON.stringify(evidence,null,2)));area.append(details);
  }
}

$('delivery-mode').onchange=controls;
