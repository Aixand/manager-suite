"""Exercise an isolated local MCP server; requires the optional mcp Python SDK.

Run with no arguments. The internal --serve mode is used only by its stdio child.
No credentials, network services, providers, or models are used.
"""
import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def serve():
    # Minimal deterministic JSON-RPC fixture, not a production MCP server.
    import json
    for line in sys.stdin:
        request = json.loads(line)
        if 'id' not in request:
            continue
        method, params = request['method'], request.get('params', {})
        results = {
            'initialize': {'protocolVersion': params.get('protocolVersion', '2025-03-26'),
                           'capabilities': {'tools': {}, 'resources': {}, 'prompts': {}},
                           'serverInfo': {'name': 'documentation-fixture', 'version': '1'}},
            'tools/list': {'tools': [{'name': 'add', 'inputSchema': {'type': 'object',
                           'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}},
                           'required': ['a', 'b']}}]},
            'resources/list': {'resources': [{'uri': 'fixture://greeting', 'name': 'greeting'}]},
            'resources/read': {'contents': [{'uri': 'fixture://greeting', 'text': 'hello from fixture'}]},
            'prompts/list': {'prompts': [{'name': 'welcome', 'arguments': [{'name': 'name', 'required': True}]}]},
            'prompts/get': {'messages': [{'role': 'user', 'content': {'type': 'text',
                            'text': 'Hello, ' + params.get('arguments', {}).get('name', '') + '!'}}]},
        }
        if method == 'tools/call':
            args = params['arguments']
            result = {'content': [{'type': 'text', 'text': str(args['a'] + args['b'])}], 'isError': False}
        else:
            result = results.get(method, {})
        print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)


async def exercise():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp_manager import Manager
    with TemporaryDirectory(prefix='mcp-doc-connected-') as temporary:
        root = Path(temporary)
        manager = Manager(root / 'store')
        manager.add('fixture', {'command': sys.executable,
                               'args': [str(Path(__file__).absolute()), '--serve']})
        result = manager.resolve(['fixture'], root, project=None, strict=True)
        row = result['servers']['fixture']
        assert result['states'][0]['status'] == 'configured'
        # The consumer starts the transport; resolution above did not start it.
        parameters = StdioServerParameters(command=row['command'], args=row['args'], cwd=row['cwd'])
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                assert 'add' in [tool.name for tool in (await client.list_tools()).tools]
                called = await client.call_tool('add', {'a': 2, 'b': 3})
                assert not called.isError
                assert any(getattr(block, 'text', '') == '5' for block in called.content)
                assert 'fixture://greeting' in [str(resource.uri) for resource in (await client.list_resources()).resources]
                resource = await client.read_resource('fixture://greeting')
                assert resource.contents[0].text == 'hello from fixture'
                assert 'welcome' in [prompt.name for prompt in (await client.list_prompts()).prompts]
                prompt = await client.get_prompt('welcome', {'name': 'Reader'})
                assert prompt.messages[0].content.text == 'Hello, Reader!'
        print('PASS: initialized stdio; listed/called tool, read resource, retrieved prompt; closed client')


if __name__ == '__main__':
    if sys.argv[1:] == ['--serve']:
        serve()
    elif not sys.argv[1:]:
        asyncio.run(asyncio.wait_for(exercise(), timeout=30))
    else:
        raise SystemExit('usage: python local_operations.py')
