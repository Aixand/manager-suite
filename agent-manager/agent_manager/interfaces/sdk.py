"""Managed launches through Undercurrent, with durable administrative leases."""
from __future__ import annotations

import asyncio
import copy
from contextlib import suppress
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from agent_manager.errors import Error
from agent_manager.interfaces.contracts import ExecutionReference, SelectionDecision, BudgetObservation, SavedSession
from agent_manager.manager import Manager
from agent_manager.providers.configuration import scoped as configuration_scope
from agent_manager.storage.locks import locks
from agent_manager.validation import name
from agent_manager.runtime.context import ThreadStore, attributes, defaults, from_record, require_metadata, session_context


from agent_manager.runtime.models import Agent, LaunchError
from agent_manager.runtime.runs import read_run, keyed_run, launch_worker, wait_for_launch, reconcile_runs, list_runs

_PROFILE_UNSET = object()


class Client:
    def __init__(self, provider="codex", *, runtime=None, config=None, root=None, labels=None, metadata=None):
        self.manager = Manager(provider, config=config, root=root)
        self._context = defaults(self.manager).resolve(labels=labels, metadata=metadata)
        if runtime is None:
            try:
                import undercurrent
            except ImportError as error:
                raise Error("Install aixand-agent-manager[undercurrent] and the Undercurrent bridge to use managed launches") from error
            runtime = undercurrent.Client()
            self._owns_runtime = True
        else:
            self._owns_runtime = False
        self.runtime = runtime

    def with_context(self, *, labels=None, metadata=None, inherit_labels=True, inherit_metadata=True):
        """A borrowed view with independent defaults and shared runtime ownership."""
        view = copy.copy(self)
        view._context = self._context.resolve(labels=labels, metadata=metadata,
            inherit_labels=inherit_labels, inherit_metadata=inherit_metadata)
        view._owns_runtime = False
        return view

    @property
    def context(self):
        return self._context.as_dict()

    async def __aenter__(self):
        try:
            await self.runtime.connect()
            await self.reconcile()
            return self
        except BaseException:
            if self._owns_runtime:
                with suppress(Exception):
                    await self.runtime.close()
            raise

    async def __aexit__(self, *_): await self.close()

    async def close(self):
        if self._owns_runtime: await self.runtime.close()

    async def providers(self):
        """Inspect native provider inventory using this client's registry context."""
        from agent_manager.providers.catalog import catalog
        return await asyncio.to_thread(catalog, config=self.manager.config_path, home=self.manager.root.parent)

    async def profiles(self):
        from agent_manager.profiles.listing import list_profiles
        return await asyncio.to_thread(list_profiles, self.manager)

    async def select_profile(self, *, model=None, args=()):
        return await asyncio.to_thread(self.manager.select_profile, model=model, args=args)

    async def validate_native_config(self, values):
        return self.manager.validate_native_config(values)

    async def agent_targets(self):
        return await asyncio.to_thread(self.manager.agent_targets)

    async def resolve_agent_target(self, environment_id):
        return await asyncio.to_thread(self.manager.resolve_agent_target, environment_id)

    async def configuration(self, profile=None, *, effective=False, refresh=True):
        return await asyncio.to_thread(self.manager.configuration, profile, effective=effective, refresh=refresh)

    async def refresh_configuration(self):
        return await asyncio.to_thread(self.manager.refresh_configuration)

    async def launch_arguments(self, profile=None, *, refresh=True):
        return await asyncio.to_thread(self.manager.launch_arguments, profile, refresh=refresh)

    async def set_launch_arguments(self, values, profile=None, *, expected_revision=None):
        return await asyncio.to_thread(self.manager.set_launch_arguments, values, profile, expected_revision=expected_revision)

    async def native_instructions(self, profile=None):
        return await asyncio.to_thread(self.manager.native_instructions, profile)

    async def set_native_instructions(self, text, profile=None, *, expected_revision=None):
        return await asyncio.to_thread(self.manager.set_native_instructions, text, profile, expected_revision=expected_revision)

    async def readiness(self, profile=None, *, refresh=False):
        return await asyncio.to_thread(self.manager.readiness, profile, refresh=refresh)

    async def budget(self, profile=None, *, refresh=False, cached=False, cwd=None, model=None) -> BudgetObservation:
        return await asyncio.to_thread(self.manager.budget, profile, refresh=refresh, cached=cached, cwd=cwd, model=model)

    async def budgets(self, *, refresh=False, cached=False, cwd=None, model=None) -> list[BudgetObservation]:
        return await asyncio.to_thread(self.manager.budgets, refresh=refresh, cached=cached, cwd=cwd, model=model)

    async def runs(self, *, include_finished=True, limit=250, labels=None):
        return await asyncio.to_thread(list_runs, self.manager, include_finished=include_finished, limit=limit, labels=labels)

    async def metadata(self, run_id):
        return from_record(await asyncio.to_thread(read_run, self.manager, run_id))["metadata"]

    async def execution_status(self, run_id):
        from agent_manager.runtime.observation import execution_status
        return await execution_status(self.manager, run_id, runtime=self.runtime)

    async def sessions(self, *, cwd=None, recursive=False, refresh=True, include_archived=False, include_children=False) -> list[SavedSession]:
        from agent_manager.profiles.sessions import listing
        return await asyncio.to_thread(listing, self.manager, cwd=cwd, recursive=recursive, refresh=refresh,
            include_archived=include_archived, include_children=include_children)

    @configuration_scope
    async def continue_session(self, session, *, profile=None, auto=False, fallback='error', **options):
        try:
            inspect.signature(Client.spawn).bind_partial(self, **options)
        except TypeError as error:
            raise Error("Unsupported native continuation option: " + str(error)) from None
        if not isinstance(session, str) or not session.strip():
            raise ValueError('session must be a native session ID or Manager session reference')
        if not isinstance(auto, bool): raise ValueError('auto must be a boolean')
        if not auto and fallback != 'error': raise ValueError('fallback requires auto=True')
        from agent_manager.runtime.launch_options import validate
        options = validate(self.manager, options, continuation=True)
        selected = decision = intent = None
        if auto:
            if profile is not None: raise Error('Choose either an explicit profile or auto=True')
            from agent_manager.runtime.automatic import options_for
            options = options_for(self, options, continuation=True)
            key = options.pop('idempotency_key', None)
            selected, key, intent, recovered = await self._automatic(options,
                fallback=fallback, idempotency_key=key, session=session)
            if recovered: return await self._recover_automatic(key, options.get('timeout', 60.0))
            decision = selected.resolve(self.manager)
            from agent_manager.profiles.selection import verify
            await asyncio.to_thread(verify, self.manager, decision)
            profile = decision.target
            options = self._selected_options(options, decision)
            options.update(idempotency_key=key, _selection_decision=decision,
                _selection_evidence=selected.to_dict(), _automatic_intent=intent)
        from agent_manager.runtime.continuation.coordinator import prepare, resolve_request
        from agent_manager.runtime.continuation.registry import for_manager
        request = await asyncio.to_thread(resolve_request, self.manager, session, profile)
        requested_cwd = options.pop('cwd', None)
        if requested_cwd is not None and Path(requested_cwd).expanduser().resolve() != Path(request.source['cwd']).resolve():
            raise Error('Continuation requires its captured working directory')
        # Read-only validation precedes any provider-specific copy/link preparation.
        context = self._context.resolve(**{key: options[key] for key in
            ('labels', 'metadata', 'inherit_labels', 'inherit_metadata') if key in options})
        context = session_context(context, 'agent', thread_id=request.source['native_id']
            if for_manager(self.manager).sdk_resume_key == 'thread_id' else None)
        attributes({**context.labels, 'agent_manager.run_id': '0' * 32,
            'agent_manager.profile': request.target}, 'labels', internal=True)
        require_metadata(self.runtime, context.metadata)
        from agent_manager.environments.operations import check_revision
        await asyncio.to_thread(check_revision, self.manager, request.target, options.get('expected_revision'))
        self.manager.launch_args(request.target,
            [*self.manager.provider_spec.sessions.resume_args, request.source['native_id']], options.get('model'))
        if not Path(request.source['cwd']).is_dir(): raise Error('Working directory does not exist')
        plan = await asyncio.to_thread(prepare, self.manager, request)
        saved = dict(plan.session)
        return await self.spawn(profile=plan.target, cwd=saved["cwd"],
            **{for_manager(self.manager).sdk_resume_key: plan.native_id},
            _source_session=saved, _continuation_plan=plan, **options)

    async def threads(self, *, profile=None, labels=None, limit=250):
        return await asyncio.to_thread(ThreadStore(self.manager).list, profile=profile, labels=labels, limit=limit)

    async def thread_metadata(self, profile, thread_id):
        row = await asyncio.to_thread(ThreadStore(self.manager).read, profile, thread_id)
        return dict(row["metadata"]) if row else {}


    async def create_profile(self, profile, *, model=None):
        return await asyncio.to_thread(self.manager.new_profile, profile, model=model)

    async def manage_profiles(self, tool, arguments, *, request_id=None):
        from agent_manager.profiles.operations import Management
        return await asyncio.to_thread(Management(self.manager).execute, tool, arguments, request_id=request_id)



    async def provider_versions(self, *, method=None):
        from agent_manager.providers.installation.releases import catalog
        return await asyncio.to_thread(catalog, self.manager.provider, method=method)

    async def plan_provider_installation(self, kind='install', *, version=None, method=None, reinstall=False):
        if kind not in ('install', 'update', 'repair'):
            raise Error('CLI installation planning requires install, update or repair')
        from agent_manager.providers.installation.plans import resolve
        return await asyncio.to_thread(resolve, self.manager, kind, version_target=version, method=method, reinstall=reinstall)

    async def prepare_provider_operation(self, arguments):
        return await asyncio.to_thread(self.manager.prepare_provider_operation, arguments)

    async def perform_provider_operation(self, operation, *, progress=None):
        return await asyncio.to_thread(self.manager.perform_provider_operation, operation, progress=progress)

    async def get_profile(self, profile):
        from agent_manager.profiles.operations import inspect_profile
        return await asyncio.to_thread(inspect_profile, self.manager, profile)

    async def delete_profile(self, profile, *, expected_revision=None):
        profile = self.manager.resolve_profile(profile)
        from agent_manager.profiles.operations import delete_profile
        return await asyncio.to_thread(delete_profile, self.manager, profile, expected_revision=expected_revision)

    async def duplicate_profile(self, profile, new_name, *, expected_revision=None):
        profile = self.manager.resolve_profile(profile)
        from agent_manager.profiles.operations import duplicate_profile
        return await asyncio.to_thread(duplicate_profile, self.manager, profile, new_name, expected_revision=expected_revision)


    async def set_model(self, profile, model, *, expected_revision=None):
        profile = self.manager.resolve_profile(profile)
        return await asyncio.to_thread(self.manager.set_model, profile, model, expected_revision=expected_revision)


















    async def models(self, profile=None, *, free=False, cwd=None, refresh=False, cached=False):
        return await asyncio.to_thread(self.manager.models, profile, free=free, cwd=cwd,
            refresh=refresh, cached=cached)

    async def integrations(self, profile=None, *, cwd=None, refresh=False, cached=False):
        return await asyncio.to_thread(self.manager.integrations, profile, cwd=cwd, refresh=refresh, cached=cached)


    async def alias_profile(self, new_name, profile=None, *, environment_id=None):
        if profile is not None: profile = self.manager.resolve_profile(profile)
        return await asyncio.to_thread(self.manager.alias_profile, new_name, profile, environment_id=environment_id)

    async def environments(self):
        return await asyncio.to_thread(self.manager.environments)

    async def get_environment(self, environment_id):
        from agent_manager.environments.operations import inspect_environment
        return await asyncio.to_thread(inspect_environment, self.manager, environment_id)

    async def forget_environment(self, environment_id, *, expected_revision=None):
        return await asyncio.to_thread(self.manager.forget_environment, environment_id, expected_revision=expected_revision)

    async def purge_environment(self, environment_id, *, expected_revision=None):
        return await asyncio.to_thread(self.manager.purge_environment, environment_id, expected_revision=expected_revision)

    async def connect_profile(self, profile, directory):
        return await asyncio.to_thread(self.manager.connect_profile, profile, directory)

    async def set_default_profile(self, profile, *, expected_revision=None):
        profile = self.manager.resolve_profile(profile)
        return await asyncio.to_thread(self.manager.set_default, profile, expected_revision=expected_revision)

    async def login(self, profile=None, *, login_args=None, expected_revision=None):
        profile = self.manager.resolve_profile(profile)
        return await asyncio.to_thread(self.manager.login, profile, login_args, expected_revision=expected_revision)

    async def login_status(self, profile=None):
        profile = self.manager.resolve_profile(profile)
        return await asyncio.to_thread(self.manager.login_status, profile)

    async def logout(self, profile=None, *, expected_revision=None):
        profile = self.manager.resolve_profile(profile)
        return await asyncio.to_thread(self.manager.logout, profile, expected_revision=expected_revision)

    async def rename_profile(self, profile, new_name, *, expected_revision=None):
        profile = self.manager.resolve_profile(profile)
        return await asyncio.to_thread(self.manager.rename, profile, new_name,
            expected_revision=expected_revision)

    async def identity_snapshot(self):
        return await asyncio.to_thread(self.manager.identity_snapshot)


    async def reconcile(self):
        """Restart administrative workers; they consult only Undercurrent's public API."""
        return await asyncio.to_thread(reconcile_runs, self.manager)

    async def get(self, run_id):
        """Recover an exact execution identity without launching a replacement."""
        from agent_manager.runtime.handle import validate
        expected = ExecutionReference.from_dict(run_id) if isinstance(run_id, dict) else run_id if isinstance(run_id, ExecutionReference) else None
        if expected is not None:
            if expected.provider != self.manager.provider or expected.registry_path != str(self.manager.state_path.resolve()):
                raise Error('Execution reference belongs to another provider or registry')
            run_id = expected.run_id
        record = await asyncio.to_thread(read_run, self.manager, run_id)
        if not record.get("session_id"):
            raise LaunchError(record.get("error") or "Launch has not returned a session", run_id)
        ref = validate(self, dict(record, run_id=run_id), expected)
        selected = SelectionDecision.from_dict(record['selection']) if record.get('selection') else None
        return Agent(run_id=run_id, provider=self.manager.provider, profile=record["agent"],
            home=record["home"], session_id=record["session_id"], thread_id=record.get('thread_id'),
            launch_kind=record.get("launch_kind", "fresh"), labels=from_record(record)["labels"],
            profile_id=record.get("profile_id"), environment_id=record.get("environment_id"),
            home_id=record.get("home_id"))._bind(self, ref, selected)

    @configuration_scope
    async def start(self, *, args=(), cwd=None, model=None, fallback='error', idempotency_key=None, **options):
        """Select and launch without UI; keyed retries retain the original decision."""
        from agent_manager.runtime.automatic import options_for
        options = options_for(self, dict(options, args=args, cwd=str(Path(Path.cwd() if cwd is None else cwd).expanduser().resolve()), model=model))
        selected, key, intent, recovered = await self._automatic(options, fallback=fallback, idempotency_key=idempotency_key)
        if recovered: return await self._recover_automatic(key, options.get('timeout', 60.0))
        decision = selected.resolve(self.manager)
        return await self.spawn(profile=decision.target, idempotency_key=key,
            _selection_decision=decision, _selection_evidence=selected.to_dict(),
            _automatic_intent=intent, **self._selected_options(options, decision))

    async def _automatic(self, options, *, fallback, idempotency_key, session=None):
        from agent_manager.runtime.automatic import plan
        return await asyncio.to_thread(plan, self, options, kind='continue' if session is not None else 'start',
            session=session, fallback=fallback, idempotency_key=idempotency_key)

    async def _recover_automatic(self, key, timeout=60.0):
        await self.runtime.connect()
        await self.reconcile()
        await wait_for_launch(self.manager, key, timeout)
        return await self.get(key)

    def _selected_options(self, options, decision):
        from agent_manager.providers.arguments import effective_model
        result = dict(options)
        if (decision.budget is not None and decision.budget['scope'] == 'provider-connection'
                and effective_model(self.manager, decision.target, result.get('args', ()), result.get('model')) is None):
            result['model'] = decision.model
        return result

    async def _thread_target(self, profile, thread_id, source_session=None):
        """Resolve identity and directory without starting or resuming a thread."""
        if source_session is not None:
            from agent_manager.profiles.sessions import check_reference, validate_source
            if source_session["native_id"] != thread_id:
                raise Error("Session source differs from the requested terminal thread")
            await asyncio.to_thread(check_reference, self.manager, source_session)
            await asyncio.to_thread(validate_source, self.manager, source_session, profile)
            return Path(source_session["cwd"]).resolve(), None
        from agent_manager.runtime.native.codex_history import find_thread
        thread = await asyncio.to_thread(find_thread, self.manager, profile, thread_id)
        if thread is None:
            raise Error("Thread was not found in the selected profile's saved history")
        return thread.cwd, None

    @configuration_scope
    async def spawn(self, *, profile=_PROFILE_UNSET, args=(), cwd=None, env=None,
                    detection_profile=None, detection=True, timeout=60.0, model=None,
                    thread_id=None, idempotency_key=None,
                    labels=None, metadata=None, inherit_labels=True, inherit_metadata=True,
                    _expected_environment_id=None, expected_revision=None,
                    resume_id=None, native_config=None, _source_session=None, _continuation_plan=None,
                    _selection_decision=None, _selection_evidence=None, _automatic_intent=None):
        """Launch a native agent with durable process and environment leases."""
        from agent_manager.providers.installation.software import require_enabled
        from agent_manager.runtime.native import arguments as native_arguments
        require_enabled(self.manager)
        from agent_manager.runtime.native.configuration import apply as apply_native_config
        from agent_manager.runtime.launch_options import validate
        validated = validate(self.manager, dict(args=args, timeout=timeout, model=model, cwd=cwd,
            env=env, native_config=native_config, detection=detection, detection_profile=detection_profile,
            idempotency_key=idempotency_key, labels=labels, metadata=metadata,
            inherit_labels=inherit_labels, inherit_metadata=inherit_metadata))
        args, env, native_config = validated['args'], validated.get('env'), validated['native_config']
        if resume_id is not None:
            from agent_manager.profiles.sessions import require_retained_resume
            require_retained_resume(self.manager)
            if self.manager.provider == 'codex' or thread_id or args:
                raise Error('Use a structured native session identity for continuation')
            if not isinstance(resume_id, str) or not resume_id or len(resume_id) > 256 or resume_id.startswith('-'):
                raise ValueError('Invalid native session identity')
        context = self._context.resolve(labels=labels, metadata=metadata,
            inherit_labels=inherit_labels, inherit_metadata=inherit_metadata)
        profile = self.manager.resolve_profile(None if profile is _PROFILE_UNSET else profile)
        from agent_manager.runtime.continuation.registry import for_manager
        if resume_id and _source_session is None:
            _continuation_plan = await asyncio.to_thread(for_manager(self.manager).resume, self.manager, profile, resume_id)
            if _continuation_plan is not None:
                _source_session = dict(_continuation_plan.session)
                resume_id = _continuation_plan.native_id
                if cwd is None: cwd = _source_session['cwd']
        if self.manager.provider == 'codex' and native_arguments.command(args)[:1] == ['resume']:
            raise Error('Use thread_id for SDK resumes')
        if thread_id is not None:
            if self.manager.provider != "codex":
                raise Error("Thread terminal launches currently require Codex terminal mode")
            if profile is None:
                raise Error("Thread launches require an explicit profile")
            try:
                thread_id = str(uuid.UUID(thread_id))
            except (ValueError, TypeError, AttributeError):
                raise ValueError("thread_id must be a UUID") from None
            if args:
                raise Error("Thread launches use structured settings; forwarded arguments are not supported")
            saved_context = None if _source_session else await asyncio.to_thread(ThreadStore(self.manager).read, profile, thread_id)
            if saved_context:
                context = self._context.resolve(**{kind: saved_context[kind] for kind in ("labels", "metadata")}).resolve(
                    labels=labels, metadata=metadata, inherit_labels=inherit_labels, inherit_metadata=inherit_metadata)
        run_id = uuid.uuid4().hex
        signature = None
        if idempotency_key is not None:
            try:
                run_id = uuid.UUID(idempotency_key).hex
            except (ValueError, TypeError, AttributeError):
                raise ValueError("idempotency_key must be a UUID") from None
            automatic_path = self.manager.root / 'automatic-starts' / (run_id + '.json')
            if automatic_path.exists():
                from agent_manager.storage.json import read
                automatic_record = read(automatic_path)
                if not isinstance(automatic_record, dict) or automatic_record.get('intent') != _automatic_intent:
                    raise Error('Idempotency key already names an automatic managed launch')
            launch_parameters = [profile.environment_id, list(args), str(cwd) if cwd is not None else (None if thread_id else os.getcwd()),
                       dict(env or {}), detection_profile, detection, model, thread_id]
            runtime_options = dict(self.runtime.connection_options)
            request = []
            if native_config: request.append({'native_config': native_config})
            if _automatic_intent is not None: request.append({'automatic_intent': _automatic_intent})
            if resume_id is not None or expected_revision is not None:
                request.append({'resume_id': resume_id, 'expected_revision': expected_revision})
            if _source_session is not None:
                request.append({"source_session": {key: _source_session.get(key) for key in
                    ("ref", "provider", "native_id", "history_home", "origin_environment_id", "cwd")}})
            if context.labels or context.metadata:
                request.append({"context": context.as_dict()})
            def signature(record=None):
                connection = runtime_options
                if record is not None:
                    connection = record.get('connection', {})
                    original_home, current_home = connection.get('home'), runtime_options.get('home')
                    if (not original_home or not current_home
                            or Path(original_home).expanduser().resolve() != Path(current_home).expanduser().resolve()):
                        raise Error('Idempotency key belongs to a different Undercurrent home')
                # Bridge paths, RPC deadlines and frame bounds describe transport,
                # not native work. Reuse the original transport salt for comparison.
                return hashlib.sha256(json.dumps(launch_parameters + [connection] + request,
                    sort_keys=True).encode()).hexdigest()
            def previous():
                with locks(self.manager.lock_root, "registry", wait=True):
                    return keyed_run(self.manager, self.manager.state(), run_id, signature)
            if await asyncio.to_thread(previous) is not None:
                await self.runtime.connect()
                await self.reconcile()
                await wait_for_launch(self.manager, run_id, timeout)
                return await self.get(run_id)
        # Recover keyed launches before applying new defaults. Derived attributes
        # are not caller intent and must not change an older launch's identity.
        context = session_context(context, "agent", thread_id=thread_id)
        launch_labels = attributes({**context.labels, "agent_manager.run_id": run_id,
                                    "agent_manager.profile": profile if profile is not None else "auto"}, "labels", internal=True)
        require_metadata(self.runtime, context.metadata)
        await self.runtime.connect()
        await self.reconcile()
        name(profile)
        launch_labels["agent_manager.profile"] = profile
        launch_labels = attributes(launch_labels, "labels", internal=True)
        directory = Path(cwd or os.getcwd()).expanduser().resolve()
        if thread_id is not None:
            saved_directory, _ = await self._thread_target(profile, thread_id, _source_session)
            if cwd is not None and directory != saved_directory:
                raise Error("Thread launch directory differs from its saved working directory")
            directory = saved_directory
        if not directory.is_dir(): raise Error(f"Working directory does not exist: {directory}")
        program = shutil.which(self.manager.config["executable"])
        if program is None: raise Error("Configured provider executable was not found")
        program = os.path.abspath(program)

        def prepare():
            # Serialize retries of one launch without holding the profile
            # registry while rendering or probing native config and hook trust.
            with locks(self.manager.lock_root, "prepare-" + run_id, wait=True):
                try:
                    prepare_locked()
                except BaseException:
                    if run_id not in self.manager.state().get('runs', {}):
                        from agent_manager.runtime.session_leases import release
                        release(self.manager, run_id)
                    raise

        def prepare_locked():
            manager = self.manager
            with locks(manager.lock_root, "registry", wait=True):
                initial_state = manager.state()
                if signature is not None and keyed_run(manager, initial_state, run_id, signature) is not None:
                    return
                manager.resolve_profile(profile)
                from agent_manager.environments.operations import check_revision
                check_revision(manager, profile, expected_revision)
                if _expected_environment_id is not None and profile.environment_id != _expected_environment_id:
                    raise Error('The original environment identity no longer exists')
            from agent_manager.runtime.execution import launch_home
            native_args = manager.launch_args(profile, [*manager.provider_spec.sessions.resume_args, resume_id] if resume_id else args, model)
            native_args = apply_native_config(manager, native_args, native_config)
            with launch_home(manager, profile, args=native_args, cwd=directory, env=env, thread_id=thread_id or resume_id, source_session=_source_session, continuation_plan=_continuation_plan) as plan:
                prepare_in_home(initial_state, plan)

        def prepare_in_home(initial_state, home_plan):
            manager = self.manager
            home = str(manager.home(profile).resolve())
            variables = {"TERM": "xterm-256color", "COLORTERM": "truecolor", **dict(env or {}),
                         **manager.environment(Path(home), cwd=directory)}
            native_id = for_manager(manager).terminal_identity(manager, profile, run_id, resume_id, _source_session, mode="terminal")
            if resume_id:
                resume_flags = list(manager.provider_spec.sessions.resume_args)
                native_args = [*resume_flags, resume_id]
            else:
                native_args = ["--session-id", native_id, *args] if native_id else args
            terminal_args = manager.launch_args(profile, [*list(manager.provider_spec.sessions.resume_args), thread_id] if thread_id else native_args, model)
            terminal_args = apply_native_config(manager, terminal_args, native_config)
            if for_manager(manager).session_ids:
                variables = manager.launch_environment(Path(home), cwd=directory, base=variables)
            variables = manager.launch_environment(Path(home), cwd=directory, base=variables)
            from agent_manager.runtime.native.launch import terminal_launch
            terminal_args, variables = terminal_launch(manager, terminal_args, variables, managed=True)
            with locks(manager.lock_root, "registry", wait=True):
                state = manager.state()
                home_plan.claim(manager, state, profile)
                if _expected_environment_id is not None and profile.environment_id != _expected_environment_id:
                    raise Error('The original environment identity no longer exists')
                if signature is not None and keyed_run(manager, state, run_id, signature) is not None:
                    return
                from agent_manager.environments.operations import check_revision
                check_revision(manager, profile, expected_revision)
                if _selection_decision is not None:
                    from agent_manager.profiles.selection import verify
                    verify(manager, _selection_decision)
                from agent_manager.runtime.names import allocate_name
                runtime_name = allocate_name(state, profile, run_id)
                state.setdefault("runs", {})[run_id] = {
                    "mode": "terminal",
                    **({"selection": _selection_evidence, "automatic_intent": _automatic_intent} if _selection_evidence else {}),
                    "thread_id": thread_id or native_id,
                    "launch_kind": "resume" if thread_id or resume_id else "fresh",
                    **({"request_signature": signature()} if signature is not None else {}),
                    "runtime_name": runtime_name,
                    "created_at_millis": int(time.time() * 1000),
                    "labels": launch_labels, "metadata": context.metadata,
                    "runtime": "undercurrent", "agent": profile, "profile_id": profile.profile_id, "environment_id": profile.environment_id,
                    **home_plan.provenance,
                    "home": home, "status": "prepared", "session_id": None,
                    "connection": self.runtime.connection_options,
                    "launch": {"command": manager.launch_command(Path(home), [program, *terminal_args]),
                               "name": runtime_name, "cwd": str(directory), "env": variables,
                               "profile": detection_profile or manager.config["detection_profile"],
                               "detection": detection,
                               "labels": launch_labels,
                               **({"metadata": context.metadata} if context.metadata else {}),
                               "idempotency_key": run_id},
                }
                manager.save(state)
            if native_id and not resume_id:
                from agent_manager.profiles.sessions import remember
                remember(manager, profile, native_id, cwd=str(directory))
            home_plan.release()
            launch_worker(manager, run_id)

        # Shield the short preparation/worker handoff from task cancellation.
        preparation = asyncio.create_task(asyncio.to_thread(prepare))
        try:
            await asyncio.shield(preparation)
        except asyncio.CancelledError:
            await preparation
            raise
        await wait_for_launch(self.manager, run_id, timeout)
        result = await self.get(run_id)
        return result


