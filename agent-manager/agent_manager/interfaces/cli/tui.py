"""Small terminal selector; terminal state is restored before launching a provider."""
import curses
import sys
import unicodedata

from agent_manager.errors import Error


def clean(value):
    return ''.join(' ' if unicodedata.category(c).startswith('C') else c for c in str(value))


def fit(value, width):
    text = clean(value)
    result, used = '', 0
    for character in text:
        size = 0 if unicodedata.combining(character) else 2 if unicodedata.east_asian_width(character) in ('W', 'F') else 1
        if used + size > width:
            break
        result += character
        used += size
    return result + ' ' * (width - used)


def require_terminal():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise Error('Selection requires an interactive terminal; supply explicit command options')


def prompt(title, label, *, default=''):
    """Editable single-line entry with cancellation and restored terminal state."""
    require_terminal()
    def entry(screen):
        value = list(default)
        screen.keypad(True)
        while True:
            height, width = screen.getmaxyx()
            screen.erase()
            for row, content in enumerate((title, label, ''.join(value), 'Enter: accept | Esc: cancel | Backspace: erase')):
                if row < height - 1:
                    screen.addnstr(row, 0, clean(content), max(0, width - 1))
            screen.refresh()
            key = screen.get_wch()
            if key == '\x1b': return None
            if key in ('\n', '\r'): return ''.join(value).strip()
            if key in (curses.KEY_BACKSPACE, '\b', '\x7f'):
                if value: value.pop()
            elif isinstance(key, str) and key.isprintable() and len(value) < 4096:
                value.append(key)
    try:
        return curses.wrapper(entry)
    except curses.error:
        raise Error('Unable to open terminal input; check TERM and terminal size') from None


def pick(title, headers, rows, detail='', *, disabled=(), initial=0):
    require_terminal()
    if not rows:
        return None
    try:
        return curses.wrapper(_screen, title, headers, rows, detail, disabled, initial)
    except curses.error:
        raise Error('Unable to open the terminal selector; check TERM and terminal size') from None


def _screen(screen, title, headers, rows, detail, disabled=(), initial=0):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    selected, offset = initial, 0
    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        def write(y, text, attr=0):
            if 0 <= y < height:
                try:
                    screen.addstr(y, 0, fit(text, max(0, width - 1)), attr)
                except curses.error:
                    pass
        if height < 9 or width < 75:
            write(0, 'Resize terminal to at least 75 columns x 9 rows, or press q to cancel.')
        else:
            write(0, title, curses.A_BOLD)
            write(1, detail)
            # Keep date/time intact; give surplus width to session names.
            if len(headers) == 5:
                widths = [width - 1 - 8 - 12 - 12 - 10 - 16, 12, 12, 10, 16]
            elif headers in (['Action', 'Details'], ['Provider', 'Details']):
                widths = [24, width - 1 - 2 - 24]
            elif len(headers) == 2:
                widths = [max(1, width - 1 - 2 - 14), 14]
            elif headers == ['Profile', 'Budget', 'Native home']:
                widths = [24, 22, width - 1 - 4 - 24 - 22]
            else:
                available = max(len(headers), width - 1 - 2 * (len(headers) - 1))
                widths = [available // len(headers)] * len(headers)
                widths[0] += available % len(headers)
            def line(values):
                return '  '.join(fit(value, size) for value, size in zip(values, widths))
            write(3, line(headers), curses.A_BOLD)
            page = height - 7
            offset = min(offset, selected)
            offset = max(offset, selected - page + 1)
            for position in range(offset, min(len(rows), offset + page)):
                attr = curses.A_REVERSE if position == selected else 0
                if position in disabled:
                    attr |= curses.A_DIM
                write(4 + position - offset, line(rows[position]), attr)
            write(height - 3, ' | '.join(clean(value) for value in rows[selected]))
            write(height - 2, f'{selected + 1}/{len(rows)}  Up/Down: select  PgUp/PgDn: page  Enter: select  Esc/q: cancel')
        screen.refresh()
        key = screen.getch()
        if key in (27, ord('q'), 3):
            return None
        if height < 9 or width < 75:
            continue
        if key in (10, 13, curses.KEY_ENTER):
            if selected not in disabled:
                return selected
        if key in (curses.KEY_UP, ord('k')):
            selected = max(0, selected - 1)
        elif key in (curses.KEY_DOWN, ord('j')):
            selected = min(len(rows) - 1, selected + 1)
        elif key == curses.KEY_NPAGE:
            selected = min(len(rows) - 1, selected + height - 7)
        elif key == curses.KEY_PPAGE:
            selected = max(0, selected - height + 7)
        elif key == curses.KEY_HOME:
            selected = 0
        elif key == curses.KEY_END:
            selected = len(rows) - 1
