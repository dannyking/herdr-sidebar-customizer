"""Saved SSH machines: machine labels and native fallback text in the Agents layout."""
from contextlib import ExitStack
from pathlib import Path
import shutil
import tempfile
import tomllib
import unittest
from unittest.mock import Mock, patch

import agent_info as info
import fallback
import installation as install
import sidebar_settings as prefs

M = fallback.MARKER


def agents_rows(settings=None):
    text = prefs.render_config('', prefs.DEFAULTS | (settings or {}))
    return tomllib.loads(text)['ui']['sidebar']['agents']['rows']


def visible(token, value):
    """Mimic Herdr's documented first-match hide rules for one token occurrence."""
    for rule in token.get('rules', []):
        if 'equals' in rule and value == rule['equals'] or 'contains' in rule and rule['contains'] in value:
            return not rule.get('hide')
    return True


class LayoutTests(unittest.TestCase):
    def test_machine_label_follows_the_space_name_by_default(self):
        first = agents_rows()[0]
        tokens = [t['token'] for t in first if isinstance(t, dict)]
        # After the twelve colored name tokens, before the tab name.
        self.assertEqual(tokens[12:], ['machine', 'tab'])
        machine = first[12]
        self.assertTrue(machine['bold'])
        self.assertFalse(visible(machine, 'Local'))
        self.assertTrue(visible(machine, 'build-box'))
        # The label compares the exact text, so a machine named "Local box" still shows.
        self.assertTrue(visible(machine, 'Local box'))
        self.assertNotIn('machine', [t['token'] for t in agents_rows()[1]])

    def test_machine_label_can_lead_the_details_row(self):
        rows = agents_rows({'machine_position': 'details', 'machine_colour': '#445566'})
        self.assertEqual([t['token'] for t in rows[1]], ['machine', '$agent_info', 'agent', 'state_text'])
        self.assertEqual(rows[1][0], {'token': 'machine', 'dim': False, 'bold': True, 'fg': '#445566',
                                      'rules': [{'equals': 'Local', 'hide': True}]})
        self.assertNotIn('machine', [t['token'] for t in rows[0] if isinstance(t, dict)])
        with self.assertRaises(ValueError):
            prefs.validate({'machine_position': 'footer'})

    def test_native_text_hides_only_on_marked_agents(self):
        row = agents_rows()[1]
        self.assertEqual(row[0]['token'], '$agent_info')
        native = {t['token']: t for t in row[1:]}
        self.assertEqual(set(native), {'agent', 'state_text'})
        marked = fallback.presentation({'pane_id': 'w1:p1', 'agent': 'claude'})
        self.assertFalse(visible(native['agent'], marked['display_agent']))
        self.assertFalse(visible(native['state_text'], marked['state_labels']['working']))
        # A machine without the plugin reports plain native values.
        self.assertTrue(visible(native['agent'], 'claude'))
        self.assertTrue(visible(native['state_text'], 'idle'))

    def test_fallback_survives_optional_rows_and_respects_colors(self):
        rows = agents_rows({'agent_text': False, 'show_tab': False, 'agent_colour': '#112233'})
        self.assertEqual([t['token'] for t in rows[1]], ['agent', 'state_text'])
        self.assertTrue(all(t['fg'] == '#112233' for t in rows[1]))

    def test_machine_labels_can_be_turned_off(self):
        for position in ('name', 'details'):
            rows = agents_rows({'show_machine': False, 'machine_position': position})
            self.assertNotIn('machine', [t['token'] for row in rows for t in row if isinstance(t, dict)])

    @unittest.skipUnless(shutil.which('herdr'), 'needs the Herdr binary')
    def test_installed_herdr_accepts_the_rules(self):
        prefs.check_config(prefs.render_config('', prefs.DEFAULTS))


class PresentationTests(unittest.TestCase):
    def test_marks_name_and_every_state_without_changing_visible_text(self):
        wanted = fallback.presentation({'pane_id': 'w1:p1', 'agent': 'codex'})
        self.assertEqual(wanted['display_agent'], 'codex' + M)
        self.assertEqual(set(wanted['state_labels']), set(fallback.STATES))
        self.assertEqual(wanted['state_labels']['blocked'], 'blocked' + M)

    def test_preserves_another_reporters_text_and_is_idempotent(self):
        agent = {'pane_id': 'w1:p1', 'agent': 'claude', 'display_agent': 'Claude: auth',
                 'state_labels': {'working': 'refactoring auth'}}
        wanted = fallback.presentation(agent)
        self.assertEqual(wanted['display_agent'], 'Claude: auth' + M)
        self.assertEqual(wanted['state_labels']['working'], 'refactoring auth' + M)
        self.assertEqual(wanted['state_labels']['idle'], 'idle' + M)
        self.assertEqual(fallback.presentation(agent | wanted), wanted)

    def test_long_text_keeps_marker_inside_the_api_limit(self):
        wanted = fallback.presentation({'pane_id': 'w1:p1', 'agent': 'x' * 120})
        self.assertEqual(len(wanted['display_agent']), 80)
        self.assertTrue(wanted['display_agent'].endswith(M))

    def test_sync_reports_only_unmarked_agents(self):
        marked = {'pane_id': 'w1:p1', 'agent': 'claude'}
        marked |= fallback.presentation(marked)
        calls = []
        with patch.object(fallback, 'rpc', lambda e, m, p=None: calls.append((m, p)) or {}):
            fallback.sync('/test.sock', [marked, {'pane_id': 'w1:p2', 'agent': 'pi'}])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]['pane_id'], 'w1:p2')
        self.assertEqual(calls[0][1]['source'], fallback.SOURCE)
        self.assertNotIn('ttl_ms', calls[0][1])
        self.assertNotIn('tokens', calls[0][1])

    def test_non_text_labels_are_treated_as_missing(self):
        wanted = fallback.presentation({'pane_id': 'w1:p1', 'agent': 'pi', 'display_agent': 7,
                                        'state_labels': {'idle': ['x']}})
        self.assertEqual(wanted['display_agent'], 'pi' + M)
        self.assertEqual(wanted['state_labels']['idle'], 'idle' + M)
        self.assertFalse(fallback.marked({'display_agent': None, 'state_labels': 'idle'}))
        self.assertTrue(fallback.marked({'state_labels': {'idle': 'idle' + M}}))


    def test_restore_clears_fallback_presentation(self):
        calls = []
        def rpc(endpoint, method, params=None):
            calls.append((method, params))
            if method == 'agent.list': return {'agents': [{'pane_id': 'w1:p1'}]}
            if method == 'workspace.list': return {'workspaces': []}
            return {}
        with patch.object(install, 'rpc', rpc), patch.object(fallback, 'rpc', rpc):
            install.clear_metadata('/test.sock')
        self.assertIn(('pane.report_metadata', {'pane_id': 'w1:p1', 'source': fallback.SOURCE,
                                                'clear_display_agent': True, 'clear_state_labels': True}), calls)


class CollectorFallbackTests(unittest.TestCase):
    def sync(self, agents, settings=None, fallback_sync=None):
        """Run one isolated sync; returns (status, marked panes, cleared panes)."""
        with tempfile.TemporaryDirectory() as directory:
            collector = info.Collector('/test.sock', Path(directory))
            values = prefs.DEFAULTS | {'collect_details': True} | (settings or {})
            collector.settings.load = lambda: values
            collector.hook.installed = lambda: False
            collector.integrations.installed = lambda: {}
            calls = []
            def rpc(endpoint, method, params=None):
                calls.append((method, params))
                return {'agents': agents} if method == 'agent.list' else {}
            with ExitStack() as stack:
                stack.enter_context(patch.object(info, 'rpc', rpc))
                stack.enter_context(patch.object(fallback, 'rpc', rpc))
                stack.enter_context(patch.object(collector, 'discover'))
                if fallback_sync:
                    stack.enter_context(patch.object(fallback, 'sync', fallback_sync))
                status = collector.sync()
        ours = [p for m, p in calls if m == 'pane.report_metadata' and p['source'] == fallback.SOURCE]
        marked = {p['pane_id'] for p in ours if 'display_agent' in p}
        cleared = {p['pane_id'] for p in ours if p.get('clear_display_agent')}
        return status, marked, cleared

    def test_marks_only_agents_whose_row_has_details(self):
        claude = {'pane_id': 'w1:p1', 'agent': 'claude', 'agent_session': {'kind': 'id', 'value': 'abc'}}
        hermes = {'pane_id': 'w1:p2', 'agent': 'hermes'}
        unsessioned = {'pane_id': 'w1:p3', 'agent': 'codex'}
        status, marked, cleared = self.sync([claude, hermes, unsessioned])
        self.assertEqual(marked, {'w1:p1', 'w1:p3'})
        status, marked, cleared = self.sync([claude, hermes, unsessioned], {'detail_hints': False})
        self.assertEqual(marked, {'w1:p1'})

    def test_hidden_details_or_collection_off_keep_every_row_plugin_only(self):
        claude = {'pane_id': 'w1:p1', 'agent': 'claude', 'agent_session': {'kind': 'id', 'value': 'abc'}}
        hermes = {'pane_id': 'w1:p2', 'agent': 'hermes'}
        for settings in ({'agent_text': False}, {'collect_details': False}):
            with self.subTest(settings=settings):
                status, marked, cleared = self.sync([claude, hermes], settings)
                self.assertEqual(marked, {'w1:p1', 'w1:p2'})
                self.assertEqual(cleared, set())

    def test_previously_marked_rows_without_details_get_native_text_back(self):
        hermes = {'pane_id': 'w1:p2', 'agent': 'hermes'}
        hermes |= fallback.presentation(hermes)
        claude = {'pane_id': 'w1:p1', 'agent': 'claude', 'agent_session': {'kind': 'id', 'value': 'abc'}}
        claude |= fallback.presentation(claude)
        status, marked, cleared = self.sync([claude, hermes])
        self.assertEqual(cleared, {'w1:p2'})
        # Turning collection off keeps rows plugin-only: nothing is cleared.
        status, marked, cleared = self.sync([claude, hermes], {'collect_details': False})
        self.assertEqual(cleared, set())

    def test_fallback_failure_is_recorded_without_stopping_details(self):
        agents = [{'pane_id': 'w1:p1', 'agent': 'opencode'}]
        failing = Mock(side_effect=OSError)
        status, marked, cleared = self.sync(agents, fallback_sync=failing)
        failing.assert_called_once_with('/test.sock', agents)
        self.assertEqual(status['fallback_error'], 'OSError')
        self.assertEqual(status['missing_session'], {'opencode': 1})

if __name__ == '__main__':
    unittest.main()
