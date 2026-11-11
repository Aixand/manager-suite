import unittest

from conversation_manager.turn_output import TurnOutput, LIMIT


class TurnOutputTests(unittest.TestCase):
    def test_final_message_excludes_commentary_and_supports_agents_without_message_ids(self):
        output = TurnOutput()
        output.append('I will check this.')
        output.tool()
        output.append('{"outcome":"completed",')
        output.append('"report":"Café → 日本語"}')
        self.assertEqual(output.result(), {'final_text': '{"outcome":"completed","report":"Café → 日本語"}', 'truncated': False})

    def test_message_ids_and_completed_items_replace_partial_output(self):
        output = TurnOutput()
        output.append('commentary', 'first')
        output.append('partial', 'final')
        output.replace('complete report', 'final')
        self.assertEqual(output.text, 'complete report')

    def test_truncation_is_retained_until_a_new_message(self):
        output = TurnOutput()
        output.append('x' * (LIMIT + 1), 'first')
        self.assertEqual(len(output.text), LIMIT)
        self.assertTrue(output.result()['truncated'])
        output.append('report', 'second')
        self.assertEqual(output.result(), {'final_text': 'report', 'truncated': False})
