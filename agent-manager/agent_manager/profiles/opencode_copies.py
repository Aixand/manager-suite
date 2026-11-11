"""Observed revision ancestry for independent native OpenCode session copies."""
import hashlib
import json
from pathlib import Path
import time
import uuid

from agent_manager.errors import Error
from agent_manager.storage.locks import locks
from agent_manager.runtime.native import opencode_transfer as native


def ancestor(graph, older, newer):
    pending, seen = [newer], set()
    while pending:
        item = pending.pop()
        if item == older: return True
        if item in seen: continue
        seen.add(item); pending.extend(graph['revisions'].get(item, {}).get('parents', []))
    return False


def heads(graph):
    revisions = {row['revision'] for row in graph['copies'].values() if row.get('revision')}
    return {item for item in revisions if not any(item != other and ancestor(graph, item, other) for other in revisions)}


def _annotate(state, conversation_id):
    graph = state['opencode_continuation']['conversations'][conversation_id]
    latest = heads(graph)
    for reference, copy in graph['copies'].items():
        saved = state['sessions'][reference]
        status = 'unobserved'
        if copy.get('revision'):
            status = 'stale' if copy['revision'] not in latest else 'branch' if len(latest) > 1 else 'current'
        saved.update(conversation_id=conversation_id, history_revision=copy.get('revision'),
            copied_from_revision=copy.get('imported_revision'), last_observed=copy.get('observed_at'),
            copy_available=copy.get('available', False), copy_status=status)


def _observe(graph, reference, payload):
    copy = graph['copies'].setdefault(reference, {})
    current, previous = native.revision(payload), copy.get('revision')
    revision = graph['revisions'].setdefault(current, dict(parents=[]))
    if previous and previous != current and not ancestor(graph, current, previous):
        revision['parents'] = sorted(set(revision['parents']) | {previous})
    copy.update(revision=current, observed_at=time.time(), available=True)
    return current


def _identity(payload):
    return hashlib.sha256(json.dumps([row['id'] for row in payload['messages']]).encode()).hexdigest()


def _save(manager, graph, conversation_id, *, transfer=None, saved=None):
    from agent_manager.profiles.sessions import check_reference
    with locks(manager.lock_root, 'registry', wait=True):
        state = manager.state()
        for reference in graph['copies']:
            if reference not in state['sessions']: raise Error('An OpenCode copy reference changed during transfer')
        if saved is not None: check_reference(manager, saved, state=state)
        tracking = state.setdefault('opencode_continuation', dict(schema=1, conversations={}, transfers={}))
        tracking['conversations'][conversation_id] = graph
        if transfer is not None: tracking['transfers'][transfer['id']] = transfer
        _annotate(state, conversation_id); manager.save(state)


def prepare(manager, saved, profile, *, logical=False):
    """Copy a selected branch checkpoint. Source execution is never inspected."""
    from agent_manager.profiles.sessions import check_reference, reference
    from agent_manager.providers.opencode import sessions
    from agent_manager.providers.installation.software import require_enabled
    require_enabled(manager)
    profile = manager.resolve_profile(profile)
    home = manager.home(profile).resolve()
    native.require_version(manager)
    # Serialize transfers, not model turns. Environment retirement uses the
    # same environment lease; alias changes remain independent registry edits.
    with locks(manager.lock_root, 'opencode-transfer', wait=True):
        with locks(manager.lock_root, 'environment-' + profile.environment_id, wait=True):
            state = manager.state(); check_reference(manager, saved, state=state)
            if Path(state['environments'][profile.environment_id]['home']).resolve() != home:
                raise Error('The destination OpenCode environment changed')
            conversation_id = saved.get('conversation_id') or 'oc_' + saved['ref']
            tracking = state.get('opencode_continuation', {})
            graph = tracking.get('conversations', {}).get(conversation_id)
            if graph is None:
                graph = dict(origin_ref=saved['ref'], revisions={}, copies={saved['ref']: {}})
            payloads, failures = {}, {}
            for source_ref, copy in graph['copies'].items():
                source = state['sessions'][source_ref]
                try:
                    if not Path(source['history_home']).is_dir() or not Path(source['cwd']).is_dir():
                        raise Error('The native copy is unavailable')
                    payload = native.export_session(manager, source['history_home'], source['native_id'], source['cwd'])
                except Error as error:
                    copy['available'] = False
                    failures[source_ref] = str(error)
                    continue
                payloads[source_ref] = payload; _observe(graph, source_ref, payload)
            _save(manager, graph, conversation_id, saved=saved)
            start = graph['copies'][saved['ref']].get('revision')
            latest = heads(graph)
            candidates = latest if logical else {item for item in latest if start and ancestor(graph, start, item)}
            if not candidates and saved['ref'] in failures:
                raise Error('Could not export the selected OpenCode copy: ' + failures[saved['ref']])
            if len(candidates) != 1:
                choices = sorted(source_ref for source_ref, copy in graph['copies'].items() if copy.get('revision') in candidates)
                raise Error('OpenCode conversation has independent branches or unobserved history; select an exact branch session reference'
                    + (': ' + ', '.join(choices) if choices else ''))
            wanted = next(iter(candidates))
            available = [source_ref for source_ref, copy in graph['copies'].items()
                if copy.get('revision') == wanted and source_ref in payloads]
            if not available:
                raise Error('The latest observed OpenCode history is unavailable; connect its native home before continuing')
            # Prefer an equal destination copy, then the caller's selected copy.
            state = manager.state()
            available.sort(key=lambda key: (Path(state['sessions'][key]['history_home']).resolve() != home,
                key != saved['ref'], key))
            source_ref = available[0]; source = state['sessions'][source_ref]
            if Path(source['history_home']).resolve() == home:
                return dict(source)
            payload = payloads[source_ref]
            transfer_id = hashlib.sha256(json.dumps([conversation_id, wanted, profile.environment_id]).encode()).hexdigest()
            prior = manager.state().get('opencode_continuation', {}).get('transfers', {}).get(transfer_id)
            if prior is None or prior['status'] == 'complete':
                transfer = dict(id=transfer_id, status='prepared', conversation_id=conversation_id,
                    source_ref=source_ref, revision=wanted, environment_id=profile.environment_id,
                    home=str(home), native_id='ses_' + uuid.uuid4().hex, cwd=source['cwd'])
            else:
                transfer = dict(prior)
                if transfer['home'] != str(home) or transfer['cwd'] != source['cwd']:
                    raise Error('The pending native OpenCode transfer target changed')
            incoming = native.for_import(payload, transfer['native_id'])
            transfer['message_identity'] = _identity(incoming)
            _save(manager, graph, conversation_id, transfer=transfer, saved=source)
            # After a crash, a committed native import is evidence for completing
            # the same journal entry. Never overwrite or delete a native session.
            existing = {row.id for row in sessions(manager, home, profile)}
            if transfer['native_id'] not in existing:
                try: native.import_session(manager, home, incoming, source['cwd'])
                except Error:
                    if transfer['native_id'] not in {row.id for row in sessions(manager, home, profile)}: raise
            imported = native.export_session(manager, home, transfer['native_id'], source['cwd'])
            if native.revision(imported) != wanted or _identity(imported) != transfer['message_identity']:
                raise Error('The native OpenCode import did not preserve the selected history; its copy was retained for inspection')
            new_ref = reference(manager.provider, home, transfer['native_id'])
            copied = dict(ref=new_ref, provider=manager.provider, native_id=transfer['native_id'],
                origin_environment_id=profile.environment_id, history_home=str(home), cwd=source['cwd'],
                title=payload['info'].get('title') or source['title'], updated=time.time(),
                copied_from=source_ref)
            with locks(manager.lock_root, 'registry', wait=True):
                state = manager.state(); check_reference(manager, source, state=state)
                if Path(state['environments'][profile.environment_id]['home']).resolve() != home:
                    raise Error('The destination OpenCode environment changed')
                discovered = state['sessions'].get(new_ref)
                if discovered is not None and (discovered.get('conversation_id') not in (None, conversation_id)
                        or discovered['cwd'] != source['cwd']):
                    raise Error('The imported OpenCode reference is already owned')
                state['sessions'][new_ref] = copied
                graph['copies'][new_ref] = dict(revision=wanted, imported_revision=wanted,
                    copied_from=source_ref, observed_at=time.time(), available=True)
                tracking = state['opencode_continuation']
                tracking['conversations'][conversation_id] = graph
                tracking['transfers'][transfer_id] = dict(transfer, status='complete', target_ref=new_ref)
                _annotate(state, conversation_id); manager.save(state)
                return dict(state['sessions'][new_ref])


def validate_launch(manager, saved, environment_id):
    from agent_manager.profiles.sessions import check_reference, validate_source
    if manager.provider != 'opencode': raise Error('Invalid native conversation copy provider')
    check_reference(manager, saved)
    state = manager.state()
    graph = state.get('opencode_continuation', {}).get('conversations', {}).get(saved.get('conversation_id'))
    environment = state['environments'].get(environment_id)
    if (graph is None or saved['ref'] not in graph['copies'] or environment is None
            or saved['origin_environment_id'] != environment_id
            or Path(environment['home']).resolve() != Path(saved['history_home']).resolve()):
        raise Error('The captured OpenCode copy or destination environment changed')
    native.require_version(manager)
    from agent_manager.providers.opencode import database
    path = database(manager, Path(saved['history_home']))
    if path.resolve() != path: raise Error('The captured native OpenCode database was redirected')
    validate_source(manager, saved, None)


def validate_state(state):
    tracking = state.get('opencode_continuation')
    if tracking is None: return
    if (not isinstance(tracking, dict) or tracking.get('schema') != 1
            or not isinstance(tracking.get('conversations'), dict) or not isinstance(tracking.get('transfers'), dict)):
        raise Error('Invalid OpenCode continuation tracking')
    for identifier, graph in tracking['conversations'].items():
        if (not isinstance(identifier, str) or not identifier.startswith('oc_') or not isinstance(graph, dict)
                or graph.get('origin_ref') not in state['sessions'] or not isinstance(graph.get('revisions'), dict)
                or not isinstance(graph.get('copies'), dict)):
            raise Error('Invalid OpenCode conversation identity')
        for reference, copy in graph['copies'].items():
            if reference not in state['sessions'] or not isinstance(copy, dict): raise Error('Invalid OpenCode copy reference')
            if copy.get('revision') is not None and copy['revision'] not in graph['revisions']:
                raise Error('Invalid OpenCode copy revision')
        for digest, revision in graph['revisions'].items():
            if (not isinstance(digest, str) or not digest.startswith('sha256:') or not isinstance(revision, dict)
                    or not isinstance(revision.get('parents'), list)
                    or any(parent not in graph['revisions'] or parent == digest for parent in revision['parents'])):
                raise Error('Invalid OpenCode revision ancestry')
    for identifier, transfer in tracking['transfers'].items():
        if (not isinstance(transfer, dict) or transfer.get('id') != identifier
                or transfer.get('status') not in ('prepared', 'complete')
                or transfer.get('conversation_id') not in tracking['conversations']
                or transfer.get('environment_id') not in state['environments']
                or transfer.get('source_ref') not in state['sessions']):
            raise Error('Invalid OpenCode transfer journal')
