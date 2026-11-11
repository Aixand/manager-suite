"""Qualification safeguards are testable without credentials or a live daemon."""
from contextlib import asynccontextmanager, redirect_stderr
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from agent_manager import RunNotFoundError

spec = importlib.util.spec_from_file_location('live_qualification',
    Path(__file__).resolve().parents[1] / 'scripts/qualify_sdk_live.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


@asynccontextmanager
async def context(value):
    yield value


class LiveQualificationTests(unittest.IsolatedAsyncioTestCase):
    def test_live_calls_require_explicit_opt_in_and_finite_timeout(self):
        for argv in (['--profile', 'main'], ['--profile', 'main', '--allow-live', '--timeout', 'nan'],
                     ['--profile', 'main', '--allow-live', '--timeout', 'inf']):
            with self.subTest(argv=argv), patch('sys.argv', ['qualify', *argv]), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    live.arguments()
                self.assertEqual(error.exception.code, 2)

    async def test_prompt_echo_cannot_satisfy_the_response_check(self):
        agent = SimpleNamespace(send_input=AsyncMock())
        with self.assertRaises(ValueError):
            await live.exchange(agent, None, 'Repeat 12345', '12345', .1, 'delivery')
        agent.send_input.assert_not_awaited()

    async def test_echo_without_answer_times_out_instead_of_passing(self):
        agent = SimpleNamespace(send_input=AsyncMock(return_value=SimpleNamespace(foreground_revision=3)),
            inspect=AsyncMock(return_value=SimpleNamespace(lifecycle='running')),
            read_screen=AsyncMock(return_value=b'Output the sum of 17 + 29'))
        runtime = SimpleNamespace(wait_for_stability=AsyncMock())
        with patch.object(live, 'ready', AsyncMock()):
            with self.assertRaises(TimeoutError):
                await live.exchange(agent, runtime, 'Output the sum of 17 + 29', '46', .01, 'delivery')
        runtime.wait_for_stability.assert_not_awaited()

    async def test_answer_requires_runtime_readiness_after_delivery(self):
        agent = SimpleNamespace(session_id='owned',
            send_input=AsyncMock(return_value=SimpleNamespace(foreground_revision=12)),
            inspect=AsyncMock(return_value=SimpleNamespace(lifecycle='running')),
            read_screen=AsyncMock(return_value=b'\n46\n'))
        runtime = SimpleNamespace(wait_for_stability=AsyncMock())
        with patch.object(live, 'ready', AsyncMock()):
            await live.exchange(agent, runtime, 'Output the sum of 17 + 29', '46', .1, 'delivery')
        self.assertEqual(runtime.wait_for_stability.await_args.kwargs['after_revision'], 12)

    async def test_cleanup_recovers_only_owned_references_and_deduplicates_stops(self):
        reference = {'run_id': 'owned-key'}
        owned = SimpleNamespace(session_id='owned-session')
        agents = SimpleNamespace(get=AsyncMock(return_value=owned))
        with patch.object(live, 'connection', return_value=context(None)), \
                patch.object(live, 'client', return_value=context(agents)), \
                patch.object(live, 'stop', AsyncMock()) as stop:
            self.assertEqual(await live.cleanup(SimpleNamespace(timeout=30), [reference], ['owned-key']), [])
        self.assertEqual(agents.get.await_args_list[0].args, (reference,))
        self.assertEqual(agents.get.await_args_list[1].args, ('owned-key',))
        self.assertEqual(agents.get.await_count, 2)
        stop.assert_awaited_once_with(owned, 30)

    async def test_cleanup_does_not_treat_a_missing_acknowledged_run_as_absent(self):
        agents = SimpleNamespace(get=AsyncMock(side_effect=RunNotFoundError('missing')))
        with patch.object(live, 'connection', side_effect=lambda _: context(None)), \
                patch.object(live, 'client', side_effect=lambda *_: context(agents)):
            self.assertEqual(await live.cleanup(SimpleNamespace(timeout=30), [{'run_id': 'owned'}], []),
                             ['RunNotFoundError'])
            self.assertEqual(await live.cleanup(SimpleNamespace(timeout=30), [], ['unacknowledged']), [])

    async def test_preflight_cleanup_never_opens_a_runtime_connection(self):
        with patch.object(live, 'connection') as connection:
            self.assertEqual(await live.cleanup(SimpleNamespace(timeout=30), [], []), [])
        connection.assert_not_called()


    async def test_cross_profile_preflight_refuses_two_aliases_of_one_environment(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            args = SimpleNamespace(profile='main', continue_profile='short', reasoning_effort=None,
                output=Path(temporary) / 'qualification', model=None, preflight_only=False)
            readiness = dict(ready=True, installation={'version': '0.166.0'},
                             login={'state': 'signed-in'}, features={})
            profile = dict(profile='main', environment_id='same', home=temporary,
                           capabilities={'crossEnvironmentResume': True})
            agents = SimpleNamespace(readiness=AsyncMock(return_value=readiness),
                budget=AsyncMock(return_value=dict(status='ok', scope='provider-account', buckets=[])),
                select_profile=AsyncMock(return_value=dict(profile='main', selection={})),
                profiles=AsyncMock(return_value=[profile]), get_profile=AsyncMock(return_value=profile),
                spawn=AsyncMock())
            with patch.object(live, 'connection', return_value=context(None)), \
                    patch.object(live, 'client', return_value=context(agents)):
                with self.assertRaises(SystemExit):
                    await live.qualify(args)
            agents.spawn.assert_not_awaited()
            report = json.loads((args.output / 'report.json').read_text())
            self.assertEqual(report['failure'], dict(stage='preflight', exception='ValueError'))
            self.assertEqual(report['cleanup']['status'], 'passed')
