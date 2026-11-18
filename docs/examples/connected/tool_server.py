"""Explicit local MCP fixture: transactional, operation-keyed inventory allocation."""
import sqlite3
import sys
from mcp.server.fastmcp import FastMCP

server = FastMCP('local-inventory-fixture')

@server.tool()
def allocate(operation: str, units: int) -> dict:
    """Record one allocation per operation, checking identical retry intent."""
    with sqlite3.connect(sys.argv[1]) as db:
        db.execute('CREATE TABLE IF NOT EXISTS allocations (operation TEXT PRIMARY KEY, units INTEGER)')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT units FROM allocations WHERE operation=?', (operation,)).fetchone()
        if row is not None and row[0] != units:
            raise ValueError('Operation reused with different units')
        db.execute('INSERT OR IGNORE INTO allocations VALUES (?,?)', (operation, units))
        count = db.execute('SELECT count(*) FROM allocations').fetchone()[0]
    return {'operation': operation, 'units': units, 'effect_count': count}

if __name__ == '__main__':
    server.run(transport='stdio')
