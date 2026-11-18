"""Verify configuration and revision guards without executing a server."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from mcp_manager import Manager, McpError


def main():
    with TemporaryDirectory(prefix='mcp-doc-offline-') as temporary:
        root = Path(temporary)
        marker = root / 'launched'
        server = root / 'must_not_run.py'
        server.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError("unexpected launch")\n')
        manager = Manager(root / 'store')
        created = manager.add('offline', {'command': sys.executable, 'args': [str(server)], 'env_vars': ['DOCS_TOKEN']})
        changed = manager.configure('offline', {'tool_timeout_sec': 90}, expected_revision=created['revision'])
        assert changed['args'] == [str(server)]
        try:
            manager.configure('offline', {'tool_timeout_sec': 30}, expected_revision=created['revision'])
        except McpError as error:
            assert error.code == 'conflict'
        else:
            raise AssertionError('stale revision accepted')
        resolved = manager.resolve(['offline', 'offline'], root, project=None)
        assert [row['name'] for row in resolved['states']] == ['offline']
        assert resolved['states'][0]['status'] == 'configured'
        assert resolved['servers']['offline']['env_vars'] == ['DOCS_TOKEN']
        inspected = manager.inspect(['missing', 'offline'], root, project=None)
        assert inspected['states'][0]['status'] == 'not_found'
        assert 'offline' in inspected['servers']
        assert not marker.exists()
        print('PASS: resolved without launch; partial edits, stale guards, and mixed inspection verified')


if __name__ == '__main__':
    main()
