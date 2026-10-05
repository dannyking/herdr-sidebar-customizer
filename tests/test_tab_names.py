"""Agent tab names: topics from terminal titles, the title-change rule and pins."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent_info as info
import fallback
import sidebar_settings as prefs
import tab_names


class TopicTests(unittest.TestCase):
    def test_titles_become_topics(self):
        for title, expected in [
            ('Refactor auth middleware', 'Refactor auth middleware'),
            ('⠋ Fix flaky test', 'Fix flaky test'),
            ('✳ Review docs', 'Review docs'),
            ('[ ! ] Action Required | Design task database | example-repo', 'Design task database'),
            ('<command-name>/rename</command-name> Ship release notes', 'Ship release notes'),
            ('Tidy\x1b[31m colors\x1b[0m\x07', 'Tidy colors'),
            ('\x1b]0;window title\x07Ship it', 'Ship it'),
            ('  many   spaces  ', 'many spaces'),
        ]:
            with self.subTest(title=title):
                self.assertEqual(tab_names.topic(title), expected)

    def test_product_names_fragments_and_non_text_are_not_topics(self):
        for title in ('Claude Code', '⠙ Claude Code', 'codex', 'OpenCode', 'pi', 'nt', '', None, ['x'], 42):
            with self.subTest(title=title):
                self.assertIsNone(tab_names.topic(title))

    def test_format_fills_tokens_keeps_unknown_ones_and_caps_length(self):
        tab = {'number': 5}
        self.assertEqual(tab_names.label_for('hunk', tab, '{n}· {topic}'), '5· hunk')
        self.assertEqual(tab_names.label_for('hunk', tab, '{topic} {other}'), 'hunk {other}')
        long = tab_names.label_for('x' * 300, tab, '{topic}')
        self.assertEqual(len(long), tab_names.MAX_LABEL)
        self.assertTrue(long.endswith('…'))


class NamerTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.tabs = {'w1:t1': {'tab_id': 'w1:t1', 'label': '1', 'number': 1},
                     'w1:t2': {'tab_id': 'w1:t2', 'label': '2', 'number': 2}}
        self.renames = []

    def rpc(self, endpoint, method, params=None):
        if method == 'tab.list':
            return {'tabs': list(self.tabs.values())}
        if method == 'tab.rename':
            self.renames.append((params['tab_id'], params['label']))
            self.tabs[params['tab_id']]['label'] = params['label']
        return {}

    def sync(self, agents, template='{topic}'):
        with patch.object(tab_names, 'rpc', self.rpc):
            return tab_names.TabNamer('/test.sock', self.directory).sync(agents, template)

    def agent(self, tab, title, pane='p1'):
        return {'pane_id': f'w1:{pane}', 'tab_id': tab, 'terminal_title_stripped': title}

    def test_first_sight_renames_then_a_hand_typed_name_survives_until_the_title_changes(self):
        self.sync([self.agent('w1:t1', 'Plan the migration')])
        self.assertEqual(self.renames, [('w1:t1', 'Plan the migration')])
        self.tabs['w1:t1']['label'] = 'my name'
        self.sync([self.agent('w1:t1', 'Plan the migration')])
        self.assertEqual(self.tabs['w1:t1']['label'], 'my name')
        self.sync([self.agent('w1:t1', 'Write the migration')])
        self.assertEqual(self.tabs['w1:t1']['label'], 'Write the migration')

    def test_pinned_tabs_are_never_renamed_and_unpinning_waits_for_a_new_title(self):
        self.assertTrue(tab_names.toggle_pin(self.directory, 'w1:t1'))
        self.sync([self.agent('w1:t1', 'Plan the migration')])
        self.assertEqual(self.renames, [])
        self.assertFalse(tab_names.toggle_pin(self.directory, 'w1:t1'))
        self.sync([self.agent('w1:t1', 'Plan the migration')])
        self.assertEqual(self.renames, [])
        self.sync([self.agent('w1:t1', 'Next task')])
        self.assertEqual(self.renames, [('w1:t1', 'Next task')])

    def test_first_usable_agent_names_a_shared_tab_and_product_titles_are_skipped(self):
        self.sync([self.agent('w1:t1', 'Claude Code', 'p1'), self.agent('w1:t1', 'Real task', 'p2'),
                   self.agent('w1:t1', 'Other task', 'p3')])
        self.assertEqual(self.renames, [('w1:t1', 'Real task')])

    def test_closed_tabs_lose_history_and_pins(self):
        tab_names.toggle_pin(self.directory, 'w1:t2')
        self.sync([self.agent('w1:t1', 'Plan the migration')])
        del self.tabs['w1:t1']
        del self.tabs['w1:t2']
        self.sync([])
        self.assertEqual(tab_names.State(self.directory).load(), ({}, []))

    def test_a_label_that_already_matches_is_not_renamed_again(self):
        self.tabs['w1:t1']['label'] = 'Plan the migration'
        self.assertEqual(self.sync([self.agent('w1:t1', 'Plan the migration')]), 0)
        self.assertEqual(self.renames, [])

    def test_corrupt_state_is_treated_as_empty(self):
        (self.directory / tab_names.STATE_NAME).write_text('{"tabs": [1], "pinned": "x"}')
        self.sync([self.agent('w1:t1', 'Plan the migration')])
        self.assertEqual(self.renames, [('w1:t1', 'Plan the migration')])


class CollectorTests(unittest.TestCase):
    def collect(self, settings):
        agents = [{'pane_id': 'w1:p1', 'agent': 'hermes', 'tab_id': 'w1:t1',
                   'terminal_title_stripped': 'Plan the migration'}]
        calls = []
        def rpc(endpoint, method, params=None):
            calls.append((method, params))
            if method == 'agent.list':
                return {'agents': agents}
            if method == 'tab.list':
                return {'tabs': [{'tab_id': 'w1:t1', 'label': '1', 'number': 1}]}
            return {}
        with tempfile.TemporaryDirectory() as directory:
            collector = info.Collector('/test.sock', Path(directory))
            values = prefs.DEFAULTS | settings
            collector.settings.load = lambda: values
            collector.hook.installed = lambda: False
            collector.integrations.installed = lambda: {}
            with patch.object(info, 'rpc', rpc), patch.object(tab_names, 'rpc', rpc), \
                 patch.object(fallback, 'rpc', rpc), patch.object(collector, 'sync_spaces', return_value={}):
                status = collector.sync()
        return status, [p for m, p in calls if m == 'tab.rename']

    def test_off_by_default_and_on_when_enabled(self):
        self.assertFalse(prefs.DEFAULTS['tab_names'])
        self.assertEqual(self.collect({})[1], [])
        status, renames = self.collect({'tab_names': True, 'tab_name_format': '{n}· {topic}'})
        self.assertEqual(renames, [{'tab_id': 'w1:t1', 'label': '1· Plan the migration'}])
        self.assertNotIn('tab_names_error', status)


if __name__ == '__main__':
    unittest.main()
