"""OpenCode details from a synthetic SQLite database shaped like OpenCode 1.x."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import agent_info as info
import fallback
import opencode
import sidebar_settings as prefs

SID = 'ses_example0001'
# Deep enough that json.loads raises RecursionError rather than ValueError.
TOO_DEEP = '[' * 100000
OVERRIDES = ('CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_DATA_HOME',
             'XDG_CACHE_HOME')


def database(path, model, messages=()):
    connection = sqlite3.connect(path)
    connection.execute('create table session (id text primary key, model text, title text)')
    connection.execute('create table message (id text primary key, session_id text, time_created integer, data text)')
    connection.execute('insert into session values (?, ?, ?)', (SID, json.dumps(model), 'PRIVATE TITLE'))
    for n, data in enumerate(messages):
        connection.execute('insert into message values (?, ?, ?, ?)', (f'msg{n}', SID, n, json.dumps(data)))
    connection.commit()
    connection.close()


def assistant(total=None, **tokens):
    usage = dict(tokens, **({'total': total} if total is not None else {}))
    return {'role': 'assistant', 'tokens': usage}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db, self.config, self.catalog = self.root / 'opencode.db', self.root / 'opencode.json', self.root / 'models.json'
        # The collector test builds readers from the default paths, so those must be temporary too.
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith('HERDR_') and k not in OVERRIDES}
        # Path.home() reads USERPROFILE on Windows and HOME elsewhere.
        home = {'HOME': str(self.root / 'home'), 'USERPROFILE': str(self.root / 'home')}
        self.enterContext(patch.dict(os.environ, environment | home, clear=True))

    def reader(self):
        return opencode.Reader(self.db, self.config, self.catalog)


class ReaderTests(Fixture):
    def test_model_variant_and_context_from_config_limit(self):
        database(self.db, {'id': 'local-model:27b', 'providerID': 'local-provider', 'variant': 'high'},
                 [assistant(3000), {'role': 'user'}, assistant(8192), assistant(total=0)])
        self.config.write_text(json.dumps({'provider': {'local-provider': {'models': {
            'local-model:27b': {'name': 'Local Model 27B', 'limit': {'context': 32768}}}}}}))
        observation = self.reader().observe(SID)
        self.assertEqual(observation['model'], 'Local Model 27B')
        self.assertEqual(observation['effort'], 'high')
        # The latest finished assistant message, not the in-progress zero.
        self.assertEqual(observation['context'], {'used': 8192, 'window': 32768, 'percent': 25.0})
        values = info.tokens(observation, 'opencode', prefs.DEFAULTS)
        self.assertEqual(values['agent_info'], 'OpenCode Local Model 27B · high · 25%/33k')

    def test_catalog_fills_in_and_unknown_window_omits_context(self):
        database(self.db, {'id': 'sol', 'providerID': 'cloud', 'variant': 'default'}, [assistant(1000)])
        self.catalog.write_text(json.dumps({'cloud': {'models': {'sol': {'name': 'Sol', 'limit': {'context': 0}}}}}))
        observation = self.reader().observe(SID)
        self.assertEqual(observation['model'], 'Sol')
        self.assertNotIn('effort', observation)
        self.assertNotIn('percent', observation['context'])
        self.assertIsNone(info.tokens(observation, 'opencode')['context_compact'])

    def test_sums_parts_when_total_is_absent(self):
        database(self.db, {'id': 'm', 'providerID': 'p'},
                 [{'role': 'assistant', 'tokens': {'input': 10, 'output': 5, 'cache': {'read': 3}}}])
        self.assertEqual(self.reader().observe(SID)['context'], {'used': 18})
        # A total of zero means unfinished; parts alone are counted only without a total.
        self.assertEqual(opencode.tokens_total({'input': 10, 'output': 5, 'cache': {'read': 3}}), 18)
        self.assertEqual(opencode.tokens_total('{"total": 7}'), 7)
        self.assertIsNone(opencode.tokens_total('nonsense'))

    def test_unfinished_message_without_a_total_is_skipped(self):
        database(self.db, {'id': 'm', 'providerID': 'p'},
                 [assistant(input=40), assistant(input=0, output=0, cache={'read': 0})])
        self.assertEqual(self.reader().observe(SID)['context'], {'used': 40})

    def test_uri_characters_in_the_path_are_not_uri_syntax(self):
        # Windows forbids '?' in file names but allows '#' and '%'.
        directory = self.root / ('x#y%41' if os.name == 'nt' else 'x?mode=rwc#y%41')
        directory.mkdir()
        self.db = directory / 'opencode.db'
        database(self.db, {'id': 'm', 'providerID': 'p'}, [assistant(5)])
        before = set(self.root.iterdir())
        self.assertEqual(self.reader().observe(SID)['model'], 'm')
        self.assertEqual(set(self.root.iterdir()), before)

    def test_missing_or_broken_sources_degrade_to_nothing(self):
        self.assertEqual(self.reader().observe(SID), {})
        self.db.write_text('not a database')
        self.assertEqual(self.reader().observe(SID), {})
        self.db.unlink()
        database(self.db, {'id': 'm', 'providerID': 'p'})
        self.config.write_text('{ invalid')
        self.assertEqual(self.reader().observe('ses_other'), {})
        self.assertEqual(self.reader().observe(SID)['model'], 'm')

    def test_never_reads_titles_or_message_text(self):
        database(self.db, {'id': 'm', 'providerID': 'p'}, [assistant(5)])
        self.assertNotIn('PRIVATE TITLE', json.dumps(self.reader().observe(SID)))

    def test_collector_publishes_opencode_details(self):
        database(self.db, {'id': 'local-model:27b', 'providerID': 'local-provider'}, [assistant(100)])
        agent = {'pane_id': 'w1:p1', 'agent': 'opencode', 'agent_session': {'kind': 'id', 'value': SID}}
        collector = info.Collector('/test.sock', self.root / 'state')
        collector.opencode = self.reader()
        collector.settings.load = lambda: prefs.DEFAULTS | {'collect_details': True}
        collector.hook.installed = lambda: False
        collector.integrations.installed = lambda: {}
        collector.discover = lambda wanted: None
        collector.sync_spaces = lambda agents, settings: {}
        calls = []
        def rpc(endpoint, method, params=None):
            calls.append((method, params))
            return {'agents': [agent]} if method == 'agent.list' else {}
        with patch.object(info, 'rpc', rpc), patch.object(fallback, 'rpc', rpc):
            status = collector.sync()
        self.assertEqual(status['models'], 1)
        report = next(p for m, p in calls if m == 'pane.report_metadata' and p.get('source') == info.SOURCE)
        self.assertEqual(report['tokens']['agent_info'], 'OpenCode local-model:27b')


class UntrustedInputTests(Fixture):
    def test_values_of_the_wrong_type_are_ignored(self):
        # A list variant cannot be hashed, so it would raise in the EFFORTS lookup.
        database(self.db, {'id': 'm', 'providerID': 'p', 'variant': ['high']}, [assistant(5)])
        observation = self.reader().observe(SID)
        self.assertEqual(observation['model'], 'm')
        self.assertNotIn('effort', observation)
        for model in ({'id': 'm', 'providerID': ['p']}, {'id': {'k': 'v'}, 'providerID': 'p'}, ['m']):
            self.db.unlink()
            database(self.db, model, [assistant(5)])
            self.assertEqual(self.reader().observe(SID), {})
        self.assertEqual(opencode.session_model(42), {})
        self.assertIsNone(opencode.tokens_total(42))

    def test_config_of_the_wrong_shape_is_ignored(self):
        database(self.db, {'id': 'm', 'providerID': 'p'}, [assistant(5)])
        for document in ({'provider': ['p']}, {'provider': {'p': {'models': ['m']}}},
                         {'provider': {'p': {'models': {'m': {'name': 7, 'limit': [1]}}}}}):
            self.config.write_text(json.dumps(document))
            observation = self.reader().observe(SID)
            self.assertEqual((observation['model'], observation['context']), ('m', {'used': 5}))

    def test_json_nested_too_deeply_is_ignored(self):
        database(self.db, {'id': 'm', 'providerID': 'p'}, [assistant(5)])
        self.config.write_text(TOO_DEEP)
        self.catalog.write_text(TOO_DEEP)
        self.assertEqual(self.reader().observe(SID)['model'], 'm')
        self.assertEqual(opencode.session_model(TOO_DEEP), {})
        self.assertIsNone(opencode.tokens_total(TOO_DEEP))
        connection = sqlite3.connect(self.db)
        connection.execute('update session set model = ?', (TOO_DEEP,))
        connection.commit()
        connection.close()
        self.assertEqual(self.reader().observe(SID), {})


class LabelTests(unittest.TestCase):
    def test_custom_mode_and_label_use_opencode_spelling(self):
        self.assertEqual(prefs.agent_label({}, 'opencode', prefs.DEFAULTS), 'OpenCode')
        self.assertEqual(prefs.agent_label({}, 'opencode', prefs.DEFAULTS | {'opencode_label': 'OC'}), 'OC')
        custom = prefs.DEFAULTS | {'text_mode': 'custom', 'text_template': '{agent}'}
        self.assertEqual(prefs.agent_label({}, 'opencode', custom), 'OpenCode')

    def test_machine_label_follows_agent_details_color_by_default(self):
        rows = tomllib.loads(prefs.render_config('', prefs.DEFAULTS | {'agent_colour': '#9399b2'}))
        machine = next(t for t in rows['ui']['sidebar']['agents']['rows'][0]
                       if isinstance(t, dict) and t['token'] == 'machine')
        self.assertEqual(machine['fg'], '#9399b2')
        self.assertEqual(prefs.machine_colour(prefs.DEFAULTS | {'machine_colour': '#112233'}), '#112233')
        prefs.validate({'machine_colour': 'agent'})
        with self.assertRaises(ValueError):
            prefs.validate({'agent_colour': 'agent'})


if __name__ == '__main__':
    unittest.main()
