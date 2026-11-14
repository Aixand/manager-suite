"""Opt-in real-server MCP, permissions, and settings isolation checks; no inference."""
import asyncio,json,os,signal,sys,tempfile
from pathlib import Path
from conversation_manager import AgentEnvironments,Client
from conversation_manager.shared_codex import pool_directory,owner_state,live

MCP='''import json,sys
identity=sys.argv[1]
for raw in sys.stdin:
 msg=json.loads(raw)
 if 'id' not in msg: continue
 method=msg.get('method')
 if method=='initialize': result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'isolation-check','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':identity,'description':identity,'inputSchema':{'type':'object','properties':{}}}]}
 elif method=='tools/call': result={'content':[{'type':'text','text':identity}]}
 else: result={}
 print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}),flush=True)
'''

async def main():
 with tempfile.TemporaryDirectory(prefix='codex-mcp-isolation-') as temp:
  root=Path(temp);agents=AgentEnvironments('codex');env=agents.list()[0]['environment_id'];client=Client(root/'state')
  descriptor=agents.prepare_native(env);folder=pool_directory(client.home,descriptor,root)
  refs=[];chats=[]
  try:
   for i in range(2):
    settings={'model':'gpt-6.1-sol','access':{'sandbox':'read-only' if i==0 else 'workspace-write','approvalPolicy':'on-request'},'mcp':{'servers':[{'server_name':'agent_manager_isolation','kind':'stdio','command':sys.executable,'args':['-I','-u','-c',MCP,'identity_'+str(i)],'cwd':str(root),'env_vars':[],'startup_timeout_sec':10}]}}
    receipt=await client.launch_codex(env,cwd=root,agents=agents,settings=settings,server_mode='shared',operation_id=f'launch-{i}')
    ref=(receipt.get('result') or {}).get('connection_ref') or receipt.get('recovery',{}).get('connection_ref')
    if ref:refs.append(ref)
    assert receipt['status']=='completed',receipt
    connection=await client.connect(ref);chat=await connection.create_conversation(operation_id=f'create-{i}');chats.append(chat)
   for i,chat in enumerate(chats):
    target=await chat.mcp_target()
    async with asyncio.timeout(20):
     while True:
      inventory=await chat.connection.mcp_connector().inventory(target,scope='session')
      matching=[s for s in inventory['servers'] if s['name']=='agent_manager_isolation']
      if matching and matching[0]['connection_status']=='connected':break
      await asyncio.sleep(.2)
    tools=json.dumps(matching[0]['tools'])
    assert 'identity_'+str(i) in tools,(i,tools)
    assert 'identity_'+str(1-i) not in tools,(i,tools)
    config=await chat.configuration()
    native=config['nativeSettings']
    creation = await client.operation(f'create-{i}')
    policy = creation['result']['native'].get('sandbox') or creation['result']['native'].get('sandboxPolicy')
    assert policy and policy['type'] == ('readOnly' if i==0 else 'workspaceWrite'), creation['result']['native'].keys()
    result=await chat.set_config('effort','low' if i==0 else 'high',operation_id=f'effort-{i}')
    assert result['status']=='completed',result
   for i,chat in enumerate(chats):
    config=await chat.configuration()
    assert config['nativeSettings']['effort']==('low' if i==0 else 'high'),config
   print(json.dumps({'same_name_mcp_isolation':'passed','mixed_permissions':'passed','settings_mutation_isolation':'passed'}),flush=True)
  finally:
   for chat in chats:await chat.disconnect()
   for i,ref in enumerate(refs):await client.terminate_runtime(ref['runtime_ref'],operation_id=f'cleanup-{i}')
   state=owner_state(folder)
   if live(state):
    os.kill(state['pid'],signal.SIGTERM)
    async with asyncio.timeout(15):
     while live(owner_state(folder)):await asyncio.sleep(.05)

if __name__ == '__main__':
 asyncio.run(main())
