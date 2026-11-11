"""Concurrent profile operations preserve leases without saved-auth synchronization."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
import unittest
from unittest.mock import patch

from agent_manager import Error
from tests.support import ProfileFixture


class ConcurrencyTests(ProfileFixture, unittest.TestCase):
    def test_simultaneous_creation_of_one_label_registers_one_profile(self):
        entered = threading.Barrier(2)
        def create():
            entered.wait(timeout=3)
            try:
                return self.manager.new_profile('work')
            except Error:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(list(self.manager.state()['profiles']), ['work'])
        self.assertEqual(len(list(self.manager.managed_homes.iterdir())), 1)

    def test_running_profile_blocks_its_login_but_allows_unrelated_profile_work(self):
        self.create(); self.create('other')
        errors = []
        def run():
            try:
                self.assertEqual(self.manager._run_native('work', []), 0)
            except BaseException as error:
                errors.append(error)
        with patch.dict(os.environ, FAKE_RUN_SLEEP='.6'):
            worker = threading.Thread(target=run); worker.start()
            try:
                deadline = time.monotonic() + 4
                while not (self.manager.home('work') / 'observed.json').exists():
                    if time.monotonic() > deadline: self.fail('Disposable provider did not start')
                    time.sleep(.01)
                with self.assertRaisesRegex(Error, 'Stop this profile'):
                    self.manager.login('work')
                self.assertEqual(self.manager.login('other'), 0)
                self.manager.new_profile('third')
                self.assertTrue(worker.is_alive())
            finally:
                worker.join(timeout=4)
        self.assertFalse(worker.is_alive()); self.assertEqual(errors, [])
        self.assertFalse(self.manager.state()['runs'])
