"""Retaining the web bridge must not implicitly terminate agent workers."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import uuid

spec=importlib.util.spec_from_file_location('reference_chat_app',Path(__file__).resolve().parent.parent/'app.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BridgeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_retains_workers_and_cleanup_ignores_inherited_startups(self):
        with tempfile.TemporaryDirectory() as home:
            args=SimpleNamespace(state=home,agent_config=None,agent_root=None,live=False,stop_workers_on_exit=False)
            first=module.App(args)
            second=None
            record=None
            probe=None
            try:
                record=await first.start({'agent':'demo','start_id':str(uuid.uuid4()),'cwd':home,'model':''})
                chat=await first.get(record['id'])
                sent=await chat.send('hold',operation_id='lifecycle-send')
                await first.shutdown()
                second=module.App(SimpleNamespace(**{**vars(args),'stop_workers_on_exit':True}))
                attached=await second.get(record['id'])
                self.assertEqual((await attached.state())['active_turn']['turn_id'],sent['result']['turn_id'])
                # This bridge did not request the startup, so its cleanup flag cannot own it.
                await second.shutdown()
                probe=await first.client.attach_conversation(chat.reference)
                self.assertEqual((await probe.state())['active_turn']['turn_id'],sent['result']['turn_id'])
            finally:
                if probe:
                    await probe.disconnect()
                first.args.stop_workers_on_exit=True
                await first.shutdown()
