"""Quota units, unknown values, native envelopes and immutable public observations."""
import json
import math
import unittest

from agent_manager.budgets import adapters
from agent_manager.budgets.models import BudgetError, bucket, timestamp, validate_buckets


def claude_report(limits):
    return '\n'.join(json.dumps(row) for row in (
        {'local_command_run': {'command': 'usage'}, 'usage_report': {'rate_limits': {'limits': limits}}},
        {'type': 'result', 'local_command': 'usage', 'num_turns': 0, 'duration_api_ms': 0,
         'total_cost_usd': 0, 'is_error': False}))


def antigravity_report(buckets):
    return json.dumps({'status': 'SUCCESS', 'num_turns': 0, 'usage': {},
        'command': {'name': 'usage', 'data': {'groups': [{'name': 'Gemini', 'buckets': buckets}]}}})


class BudgetFormatsTests(unittest.TestCase):
    def test_codex_preserves_multiple_limit_ids_windows_and_credits(self):
        rows = adapters.parse_codex({'rateLimitsByLimitId': {
            'codex': {'primary': {'usedPercent': 25, 'resetsAt': 123, 'windowDurationMins': 300},
                      'credits': {'balance': '12.50', 'unlimited': False}},
            'review': {'secondary': {'usedPercent': 100}}}})
        self.assertEqual([row['id'] for row in rows], ['codex:primary', 'codex:credits', 'review:secondary'])
        self.assertEqual(rows[0]['remaining_percent'], 75)
        self.assertEqual(rows[0]['window_seconds'], 18000)
        self.assertEqual((rows[1]['remaining'], rows[1]['unit']), (12.5, 'credits'))
        self.assertEqual(rows[2]['status'], 'exhausted')

    def test_codex_single_bucket_and_absent_percent_remain_unknown(self):
        rows = adapters.parse_codex({'rateLimits': {'primary': {'resetsAt': None}}})
        self.assertIsNone(rows[0]['remaining_percent'])
        self.assertEqual(rows[0]['status'], 'unknown')
        self.assertEqual(adapters.parse_codex({}), [])

    def test_claude_model_and_global_windows_keep_distinct_identities(self):
        rows = adapters.parse_claude(claude_report([
            {'kind': 'session', 'percent': 10}, {'kind': 'weekly_all', 'percent': None},
            {'kind': 'weekly_scoped', 'percent': 100, 'scope': {'model': {'display_name': 'Opus'}}},
            {'kind': 'weekly_scoped', 'percent': 30, 'scope': {'model': {'display_name': 'Sonnet'}}}]))
        self.assertEqual(rows[0]['remaining_percent'], 90)
        self.assertEqual(rows[1]['status'], 'unknown')
        self.assertEqual(rows[2]['status'], 'exhausted')
        self.assertNotEqual(rows[2]['id'], rows[3]['id'])
        self.assertEqual(rows[3]['scope'], {'model': 'Sonnet'})

    def test_native_command_envelopes_reject_model_output(self):
        output = claude_report([]).splitlines()
        final = json.loads(output[-1]); final['num_turns'] = 1
        with self.assertRaises(BudgetError): adapters.parse_claude(output[0] + '\n' + json.dumps(final))
        output = json.loads(antigravity_report([])); output['usage']['total_tokens'] = 1
        with self.assertRaises(BudgetError): adapters.parse_antigravity(json.dumps(output))

    def test_antigravity_fraction_amount_units_disabled_and_unknown(self):
        rows = adapters.parse_antigravity(antigravity_report([
            {'id': 'weekly', 'window': 'weekly', 'remaining_fraction': .25},
            {'id': 'credits', 'window': 'monthly', 'remaining_amount': 12, 'unit': 'credits'},
            {'id': 'unknown-unit', 'window': 'monthly', 'remaining_amount': 12},
            {'id': 'off', 'window': 'weekly', 'disabled': True}]))
        self.assertEqual(rows[0]['remaining_percent'], 25)
        self.assertEqual((rows[1]['unit'], rows[1]['remaining']), ('credits', 12))
        self.assertEqual(rows[2]['status'], 'unknown')
        self.assertEqual(rows[3]['status'], 'disabled')

    def test_copilot_unlimited_and_known_request_counts(self):
        rows = adapters.parse_copilot({'quotaSnapshots': {
            'premium_interactions': {'entitlementRequests': 100, 'usedRequests': 125,
                                     'remainingPercentage': 0, 'resetDate': '2100-01-01T00:00:00Z'},
            'chat': {'entitlementRequests': -1, 'usedRequests': 5},
            'unknown': {}, 'zero': {'entitlementRequests': 0, 'usedRequests': 0}}})
        self.assertEqual(rows[0]['remaining'], 0)
        self.assertEqual(rows[0]['status'], 'exhausted')
        self.assertEqual(rows[0]['unit'], 'requests')
        self.assertEqual(rows[1]['status'], 'unlimited')
        self.assertEqual(rows[2]['status'], 'unknown')
        self.assertEqual(rows[3]['status'], 'unknown')

    def test_missing_quotas_do_not_infer_allowance(self):
        self.assertEqual(adapters.parse_copilot({}), [])
        self.assertEqual(adapters.parse_claude(claude_report([])), [])
        self.assertEqual(adapters.parse_claude(claude_report(None)), [])
        self.assertEqual(adapters.parse_antigravity(antigravity_report([])), [])

    def test_claude_extra_usage_preserves_spending_caps_and_converts_only_declared_usd(self):
        messages = claude_report([]).splitlines()
        report = json.loads(messages[0])
        report['usage_report']['rate_limits']['extra_usage'] = {
            'is_enabled': True, 'monthly_limit': 5000, 'used_credits': 1250, 'utilization': 25, 'currency': 'USD'}
        rows = adapters.parse_claude(json.dumps(report) + '\n' + messages[1])
        self.assertEqual(rows[0]['kind'], 'spending-cap')
        self.assertEqual((rows[0]['unit'], rows[0]['used'], rows[0]['limit'], rows[0]['remaining']), ('usd', 12.5, 50, 37.5))
        report['usage_report']['rate_limits']['extra_usage']['currency'] = 'EUR'
        rows = adapters.parse_claude(json.dumps(report) + '\n' + messages[1])
        self.assertEqual(rows[0]['unit'], 'percent')
        self.assertIsNone(rows[0]['remaining'])

    def test_invalid_numbers_dates_and_control_characters_are_rejected(self):
        for value in (True, -1, 101, '10', math.inf, math.nan):
            with self.subTest(value=value), self.assertRaises(BudgetError):
                bucket('one', 'One', remaining_percent=value)
        for value in (True, -1, '2100-01-01', 'tomorrow'):
            with self.subTest(value=value), self.assertRaises(BudgetError): timestamp(value)
        self.assertEqual(timestamp('2100-01-01T00:00:00Z'), 4102444800)
        with self.assertRaises(BudgetError): bucket('one', 'bad\x1b[0m')

    def test_cache_validation_rejects_injected_fields_duplicates_and_oversized_collections(self):
        row = bucket('one', 'One')
        with self.assertRaises(BudgetError): validate_buckets([dict(row, token='private')])
        with self.assertRaises(BudgetError): validate_buckets([row, row])
        with self.assertRaises(BudgetError): validate_buckets([bucket(str(i), 'One') for i in range(129)])

    def test_native_authentication_errors_never_expose_raw_details(self):
        with self.assertRaises(BudgetError) as caught:
            adapters.parse_antigravity(json.dumps({'status': 'ERROR', 'error': 'Unauthorized private-token'}), 1)
        self.assertEqual(caught.exception.code, 'authentication')
        self.assertNotIn('private-token', str(caught.exception))


if __name__ == '__main__': unittest.main()
