"""Rebuildable provider transcript, independent of the delivery/event journal."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import json
import time

from .models import ConversationError


def codex_items(snapshot, turn_ids):
    thread = snapshot.get("thread", {})
    if "turns" not in thread:
        raise ConversationError("history_unavailable", "Codex did not return saved turns")
    items = []
    for turn in thread["turns"]:
        if "items" not in turn or turn.get("itemsView") in ("notLoaded", "summary"):
            raise ConversationError("history_unavailable", "Codex returned incomplete turn items")
        turn_id = turn_ids.get(turn["id"], turn["id"])
        for item in turn["items"]:
            kind = item["type"]
            row = dict(id=turn_id + ":" + item["id"], turnId=turn_id,
                       nativeTurnId=turn["id"], nativeItemId=item["id"])
            if kind == "userMessage":
                text = []
                for part in item.get("content", []):
                    if part.get("type") == "text":
                        text.append(part.get("text", ""))
                    elif part.get("type") in ("image", "localImage"):
                        text.append("[Image]")
                    else:
                        text.append("[" + part.get("type", "attachment") + "]")
                row.update(type="userMessage", text="\n".join(text))
            elif kind == "agentMessage":
                row.update(type="agentMessage", text=item.get("text", ""))
            elif kind == "reasoning":
                row.update(type="reasoning", text="\n".join(item.get("summary", []) + item.get("content", [])))
            else:
                # Keep provider activity and identifiers, including unfamiliar
                # item types; never silently discard a saved transcript item.
                row.update(type="commandExecution" if kind == "commandExecution" else "mcpToolCall",
                           text=json.dumps(item, ensure_ascii=False), status=item.get("status"))
                row["id"] = turn_id + ":tool:" + item["id"]
                if kind == "commandExecution":
                    row.update(command=item.get("command"), exitCode=item.get("exitCode"))
            items.append(row)
    return dict(source="provider", provider="codex", complete=True, items=items)


class TranscriptView:
    """Shared native baseline plus newer observed items; never a second transcript DB."""
    refresh_interval = 2.0

    def __init__(self):
        self.cursor = 0
        self.turn_ids = {}
        self.live = OrderedDict()
        self.live_bytes = 0
        self.snapshot = None
        self.provider_cursor = -1
        self.next_refresh = 0.0
        self.failures = 0
        self.unavailable = False
        self.active_turn = None
        self.lock = asyncio.Lock()

    async def journal(self, conversation):
        while True:
            page = await conversation.connection.client.history(conversation.reference, cursor=self.cursor, limit=500)
            for event in page['entries']:
                turn, native_turn = event.get('turn_id'), event.get('native_turn_id')
                if turn and native_turn:
                    self.turn_ids[native_turn] = turn
                payload, kind = event.get('payload') or {}, event['kind']
                # Delivery receipts are not additional provider messages. Native
                # user item notifications supply the stable identity for steering.
                if kind not in ('message.delta', 'message.completed') or not payload.get('item_id'):
                    continue
                role = payload.get('role', 'assistant')
                if role not in ('user', 'assistant'):
                    continue
                key = str(turn) + ':' + payload['item_id']
                if key not in self.live:
                    self.live[key] = dict(id=key, turnId=turn,
                        type='userMessage' if role == 'user' else 'agentMessage', text='')
                    self.live_bytes += 256
                item = self.live[key]
                self.live_bytes -= 2 * len(item['text'])
                text = payload.get('text', '')
                item['text'] = text if kind == 'message.completed' else item['text'] + text
                item['cursor'] = event.get('history_cursor', page['cursor'])
                if len(item['text']) > 250000:
                    item.update(text=item['text'][:250000], truncated=True)
                self.live_bytes += 2 * len(item['text'])
                while len(self.live) > 1 and (len(self.live) > 512 or self.live_bytes > 8 * 1024 * 1024):
                    _, removed = self.live.popitem(last=False)
                    self.live_bytes -= 256 + 2 * len(removed['text'])
            if page['has_more'] and page['cursor'] <= self.cursor:
                raise ConversationError('integrity', 'History cursor failed to advance')
            self.cursor = page['cursor']
            if not page['has_more']:
                break

    def merge(self, saved):
        """Insert missing messages before their next known journal neighbor."""
        known = {item['id']: item for item in saved}
        before, anchor, missing = {}, None, []
        pending = self.cursor > self.provider_cursor
        for key, live in reversed(self.live.items()):
            native = known.get(key)
            if native is not None:
                anchor = key
                if live['type'] == 'agentMessage' and live['text'].startswith(native.get('text', '')):
                    if len(live['text']) > len(native.get('text', '')):
                        known[key] = {**native, 'text': live['text']}
                        pending = True
            else:
                item = {k: v for k, v in live.items() if k != 'cursor'}
                if anchor is None:
                    missing.append(item)
                else:
                    before.setdefault(anchor, []).append(item)
                pending = True
        result = []
        for item in saved:
            result.extend(reversed(before.get(item['id'], [])))
            result.append(known[item['id']])
        result.extend(reversed(missing))
        return result, pending

    async def read(self, conversation):
        async with self.lock:
            await self.journal(conversation)
            state = await conversation.connection.session_state(conversation.reference)
            active = (state.get('active_turn') or {}).get('turn_id')
            now = time.monotonic()
            transition = active != self.active_turn
            self.active_turn = active
            saved = codex_items(self.snapshot, self.turn_ids)['items'] if self.snapshot is not None else []
            _, pending = self.merge(saved)
            if transition or ((self.snapshot is None or pending or self.unavailable) and now >= self.next_refresh):
                watermark = self.cursor
                try:
                    async with asyncio.timeout(3):
                        snapshot = await conversation.connection.history(conversation.reference)
                    # Validation failures must not replace a usable baseline.
                    codex_items(snapshot, self.turn_ids)
                    self.snapshot = snapshot
                    self.provider_cursor = watermark
                    self.failures = 0
                    self.unavailable = False
                except Exception:
                    self.failures += 1
                    self.unavailable = True
                self.next_refresh = now + min(30.0, self.refresh_interval * 2 ** min(self.failures, 4))
                await self.journal(conversation)
            if active and self.snapshot:
                for turn in self.snapshot.get('thread', {}).get('turns', []):
                    if turn.get('status') == 'inProgress':
                        self.turn_ids[turn['id']] = active
            saved = codex_items(self.snapshot, self.turn_ids)['items'] if self.snapshot is not None else []
            items, pending = self.merge(saved)
            return dict(source='provider', provider='codex', complete=not self.unavailable,
                        items=items, reconcile_pending=pending or self.unavailable,
                        retry_after_ms=max(250, int((self.next_refresh - time.monotonic()) * 1000)))


async def read_transcript(conversation):
    if conversation.reference['adapter_id'] != 'native-codex':
        return None
    view = getattr(conversation, '_transcript_view', None)
    if view is None:
        view = conversation._transcript_view = TranscriptView()
    return await view.read(conversation)
