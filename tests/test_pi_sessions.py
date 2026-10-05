"""pi and omp session files, shaped like real pi 1.0.0 and omp 18.4.10 sessions."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent_info as info
import fallback
import pi_sessions as pi
import sidebar_settings as prefs

# Deep enough that json.loads raises RecursionError rather than ValueError.
TOO_DEEP = '[' * 100000
OVERRIDES = ('CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_DATA_HOME',
             'XDG_CACHE_HOME', 'PI_CODING_AGENT_DIR', 'PI_CODING_AGENT_SESSION_DIR', 'PI_CONFIG_DIR')


def reply(provider, model, stop='stop', parent=None, id_=None, **usage):
    message = {'role': 'assistant', 'provider': provider, 'model': model, 'stopReason': stop,
               'content': [{'type': 'text', 'text': 'PRIVATE ANSWER'}], 'usage': usage}
    return {'type': 'message', 'id': id_, 'parentId': parent, 'message': message}


def chain(*entries):
    """Give entries ids and link each to the previous one unless a parent is set."""
    previous = None
    for n, entry in enumerate(entries):
        entry.setdefault('id', f'e{n}')
        if entry.get('id') is None:
            entry['id'] = f'e{n}'
        if 'parentId' not in entry or entry['parentId'] is None and n:
            entry['parentId'] = previous
        previous = entry['id']
    return list(entries)


class Fixture(unittest.TestCase):
    """Points the home directory at a temporary directory and clears every config or state override."""

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith('HERDR_') and k not in OVERRIDES}
        # Path.home() reads USERPROFILE on Windows and HOME elsewhere.
        home = {'HOME': str(self.home), 'USERPROFILE': str(self.home)}
        self.enterContext(patch.dict(os.environ, environment | home, clear=True))
        self.enterContext(patch.object(pi, 'package_dir', return_value=None))

    def collector(self):
        """A Collector that touches nothing outside the temporary home."""
        collector = info.Collector('/test.sock', self.home / 'state')
        collector.settings.load = lambda: prefs.DEFAULTS | {'collect_details': True}
        collector.hook.installed = lambda: False
        collector.integrations.installed = lambda: {}
        collector.discover = lambda wanted: None
        collector.sync_spaces = lambda agents, settings: {}
        return collector

    def session(self, kind, lines, name='2026-10-01T00-00-00-000Z_0001.jsonl'):
        base = self.home / ('.pi/agent' if kind == 'pi' else '.omp/agent') / 'sessions/--work--'
        base.mkdir(parents=True, exist_ok=True)
        path = base / name
        path.write_text(''.join(json.dumps(line) + '\n' for line in lines))
        return path

    def observe(self, kind, path):
        observation = pi.Reader().observe(kind, pi.allowed_path(kind, str(path)))
        observation.pop('observed_at', None)
        return observation


class PiTests(Fixture):
    def models(self, document):
        target = self.home / '.pi/agent/models.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document))

    def test_real_shape_with_custom_model(self):
        self.models({'providers': {'local-provider': {'models': [{'id': 'local:4b', 'name': 'Local 4B', 'contextWindow': 32768}]}}})
        path = self.session('pi', [{'type': 'session', 'version': 3, 'id': 's'}] + chain(
            {'type': 'model_change', 'provider': 'local-provider', 'modelId': 'local:4b', 'parentId': None},
            {'type': 'thinking_level_change', 'thinkingLevel': 'off'},
            {'type': 'message', 'message': {'role': 'user', 'content': 'PRIVATE PROMPT'}},
            reply('local-provider', 'local:4b', input=692, output=477, cacheRead=1, cacheWrite=0, totalTokens=1170)))
        observation = self.observe('pi', path)
        self.assertEqual(observation, {'model_id': 'local:4b', 'model': 'Local 4B',
                                       'context': {'used': 1170, 'window': 32768, 'percent': 1170 / 32768 * 100}})
        self.assertEqual(info.tokens(observation, 'pi', prefs.DEFAULTS)['agent_info'], 'pi Local 4B · 4%/33k')
        self.assertNotIn('PRIVATE', json.dumps(pi.Reader().files))

    def test_thinking_model_switch_aborts_and_compaction(self):
        self.models({'providers': {'p': {'models': [{'id': 'b'}]}}})
        entries = chain(
            {'type': 'model_change', 'provider': 'p', 'modelId': 'a', 'parentId': None},
            reply('p', 'a', input=10, output=5, cacheRead=0, cacheWrite=0, totalTokens=0),
            {'type': 'model_change', 'provider': 'p', 'modelId': 'b'},
            {'type': 'thinking_level_change', 'thinkingLevel': 'high'},
            reply('p', 'b', stop='aborted', totalTokens=999))
        observation = self.observe('pi', self.session('pi', entries))
        # A custom model without a window uses pi's documented default.
        self.assertEqual(observation['context'], {'used': 15, 'window': 128000, 'percent': 15 / 1280})
        self.assertEqual((observation['model_id'], observation['effort']), ('b', 'high'))
        entries.append({'type': 'compaction', 'id': 'c', 'parentId': entries[-1]['id'], 'tokensBefore': 900})
        self.assertNotIn('context', self.observe('pi', self.session('pi', entries)))

    def test_follows_the_branch_from_the_last_entry(self):
        entries = chain({'type': 'model_change', 'provider': 'p', 'modelId': 'm', 'parentId': None},
                        reply('p', 'm', totalTokens=100))
        entries.append(reply('p', 'm', parent='e0', id_='fork', totalTokens=50))
        self.assertEqual(self.observe('pi', self.session('pi', entries))['context']['used'], 50)
        # A cycle must not hang the worker.
        loop = [{'type': 'model_change', 'id': 'x', 'parentId': 'y', 'provider': 'p', 'modelId': 'm'},
                {'type': 'model_change', 'id': 'y', 'parentId': 'x', 'provider': 'p', 'modelId': 'm'}]
        self.assertEqual(self.observe('pi', self.session('pi', loop, 'loop.jsonl'))['model_id'], 'm')

    def test_overrides_store_and_catalog_windows(self):
        entries = chain({'type': 'model_change', 'provider': 'anthropic', 'modelId': 'big', 'parentId': None},
                        reply('anthropic', 'big', totalTokens=1000))
        path = self.session('pi', entries)
        self.assertNotIn('window', self.observe('pi', path)['context'])
        store = self.home / '.pi/agent/models-store.json'
        store.write_text(json.dumps({'anthropic': {'models': [{'id': 'big', 'name': 'Big', 'contextWindow': 200000}]}}))
        self.assertEqual(self.observe('pi', path)['context']['window'], 200000)
        self.models({'providers': {'anthropic': {'modelOverrides': {'big': {'contextWindow': 100000, 'name': 'Big+'}}}}})
        self.assertEqual(self.observe('pi', path)['model'], 'Big+')
        self.assertEqual(self.observe('pi', path)['context']['window'], 100000)

    def test_override_that_only_renames_keeps_the_catalog_window(self):
        entries = chain({'type': 'model_change', 'provider': 'anthropic', 'modelId': 'big', 'parentId': None},
                        reply('anthropic', 'big', totalTokens=1000))
        path = self.session('pi', entries)
        self.models({'providers': {'anthropic': {'modelOverrides': {'big': {'name': 'Big (team)'}}}}})
        reader = pi.Reader()
        reader.catalogs['pi'].lookup = lambda provider, model: (200000, 'Big')
        observation = reader.observe('pi', path)
        self.assertEqual(observation['model'], 'Big (team)')
        self.assertEqual(observation['context']['window'], 200000)

    def test_catalog_from_the_installed_package(self):
        package = self.home / 'lib/node_modules/@earendil-works/pi-coding-agent'
        data = package / 'node_modules/@earendil-works/pi-ai/dist/providers/data'
        data.mkdir(parents=True)
        (data / 'anthropic.json').write_text(json.dumps({'anthropic-messages': {
            'chat:big': {'id': 'big', 'name': 'Big', 'provider': 'anthropic', 'contextWindow': 1000000}}}))
        catalog = pi.Catalog('pi')
        with patch.object(pi, 'package_dir', return_value=package):
            self.assertEqual(catalog.lookup('anthropic', 'big'), (1000000, 'Big'))
        self.assertEqual(catalog.lookup('anthropic', 'other'), (None, None))


class OmpTests(Fixture):
    def test_real_shape_title_slot_prompt_tokens_and_trailing_entries(self):
        (self.home / '.omp/agent').mkdir(parents=True)
        (self.home / '.omp/agent/models.yml').write_text(
            'providers:\n  local-provider:\n    apiKey: "secret"  # comment\n    models:\n'
            '      - id: local:4b\n        name: Local 4B\n        contextWindow: 32768\n')
        lines = [{'type': 'title', 'v': 1, 'title': 'PRIVATE TITLE', 'pad': ''},
                 {'type': 'session', 'version': 3, 'id': 's'}] + chain(
            {'type': 'model_change', 'model': 'local-provider/local:4b', 'parentId': None},
            {'type': 'thinking_level_change', 'thinkingLevel': 'medium', 'configured': None},
            reply('local-provider', 'local:4b', input=6285, output=162, cacheRead=3, cacheWrite=0, totalTokens=6450),
            {'type': 'custom', 'data': 'PRIVATE'})
        lines[4]['message']['contextSnapshot'] = {'promptTokens': 6288, 'nonMessageTokens': 6031}
        # Side calls are parented off the branch and must not become the leaf.
        lines.append({'type': 'model_usage', 'id': 'u', 'parentId': 'e0', 'provider': 'x', 'model': 'y', 'usage': {}})
        observation = self.observe('omp', self.session('omp', lines))
        self.assertEqual(observation['model'], 'Local 4B')
        self.assertEqual(observation['effort'], 'medium')
        self.assertEqual(observation['context']['used'], 6288)
        self.assertEqual(observation['context']['window'], 32768)

    def test_prompt_only_fallbacks_roles_and_null_thinking(self):
        entries = chain({'type': 'model_change', 'model': 'p/main', 'parentId': None},
                        {'type': 'model_change', 'model': 'p/small', 'role': 'smol'},
                        {'type': 'thinking_level_change', 'thinkingLevel': None})
        entries.append(reply('p', 'main', parent=entries[-1]['id'], id_='r', input=100, output=900,
                             cacheRead=20, cacheWrite=5, totalTokens=1025))
        observation = self.observe('omp', self.session('omp', entries))
        # Output is not context in omp; the smol role does not replace the model.
        self.assertEqual(observation['context']['used'], 125)
        self.assertEqual(observation['model_id'], 'main')
        self.assertNotIn('effort', observation)
        snapshot = {'contextSnapshot': {'promptTokens': 500, 'historyRewriteTokensRemoved': 120}}
        self.assertEqual(pi.context_tokens('omp', snapshot), 380)

    def test_profile_and_xdg_directories_are_allowed(self):
        profile = self.home / '.omp/profiles/work/agent/sessions/-x'
        xdg = self.home / '.local/share/omp/sessions/-x'
        for directory in (profile, xdg):
            directory.mkdir(parents=True)
            (directory / 's.jsonl').write_text('')
            self.assertIsNotNone(pi.allowed_path('omp', str(directory / 's.jsonl')))
        self.assertIsNone(pi.allowed_path('pi', str(profile / 's.jsonl')))


class SafetyTests(Fixture):
    def test_only_jsonl_inside_the_harness_roots(self):
        inside = self.session('pi', [])
        outside = self.home / 'elsewhere.jsonl'
        outside.write_text('')
        escape = inside.parent / 'link.jsonl'
        escape.symlink_to(outside)
        self.assertEqual(pi.allowed_path('pi', str(inside)), inside)
        for value in (str(outside), str(escape), str(inside.with_suffix('.txt')), 'relative.jsonl',
                      str(inside.parent / '../../../../elsewhere.jsonl'), None):
            self.assertIsNone(pi.allowed_path('pi', value))
        # Reported before the first turn creates it: accepted, but nothing to read yet.
        pending = inside.parent / 'pending.jsonl'
        self.assertEqual(pi.allowed_path('pi', str(pending)), pending)
        self.assertEqual(pi.Reader().observe('pi', pending), {})
        custom = self.home / 'custom'
        custom.mkdir()
        (custom / 'a.jsonl').write_text('')
        with patch.dict(os.environ, {'PI_CODING_AGENT_SESSION_DIR': str(custom)}):
            self.assertIsNotNone(pi.allowed_path('pi', str(custom / 'a.jsonl')))

    def test_incremental_reads_partial_lines_and_rewrites(self):
        path = self.session('pi', [])
        reader = pi.SessionFile(path)
        first = json.dumps({'type': 'model_change', 'id': 'a', 'parentId': None, 'provider': 'p', 'modelId': 'm'})
        path.write_text(first + '\n' + first[:10])
        reader.update()
        self.assertEqual(reader.order, ['a'])
        with path.open('a') as handle:
            handle.write(first[10:].replace('"a"', '"b"', 1) + '\n')
        reader.update()
        self.assertEqual(reader.order, ['a', 'b'])
        path.write_text(first + '\n')  # Shorter: rewritten from the start.
        reader.update()
        self.assertEqual(reader.order, ['a'])

    def test_keeps_reading_past_the_per_read_cap(self):
        line = lambda id_: json.dumps({'type': 'model_change', 'id': id_, 'parentId': None,
                                       'provider': 'p', 'modelId': id_}) + '\n'
        path = self.session('pi', [])
        path.write_text(line('a') + line('b') + line('c'))
        reader = pi.SessionFile(path)
        with patch.object(pi, 'MAX_BYTES', 150):
            reader.update()
            self.assertEqual(reader.order, ['a', 'b', 'c'])
            with path.open('a') as handle:
                handle.write(line('d'))
            reader.update()
        self.assertEqual(reader.order, ['a', 'b', 'c', 'd'])

    def test_skips_a_single_entry_larger_than_the_cap(self):
        entry = lambda id_, pad='': json.dumps({'type': 'message', 'id': id_, 'pad': pad}) + '\n'
        path = self.session('pi', [])
        path.write_text(entry('a') + entry('huge', 'x' * 500))
        reader = pi.SessionFile(path)
        with patch.object(pi, 'MAX_BYTES', 100):
            reader.update()
            self.assertLessEqual(len(reader.partial), 100)
            with path.open('a') as handle:
                handle.write(entry('b'))
            reader.update()
        self.assertEqual(reader.order, ['a', 'b'])

    def test_collector_reads_validated_paths_only(self):
        path = self.session('pi', chain({'type': 'model_change', 'provider': 'p', 'modelId': 'm', 'parentId': None},
                                        reply('p', 'm', totalTokens=10)))
        good = {'pane_id': 'w1:p1', 'agent': 'pi', 'agent_session': {'kind': 'path', 'value': str(path)}}
        bad = {'pane_id': 'w1:p2', 'agent': 'omp', 'agent_session': {'kind': 'path', 'value': str(path)}}
        self.assertEqual(info.session_ref(good), path)
        self.assertIsNone(info.session_ref(bad))
        self.assertIsNone(info.session_ref({'agent': 'pi', 'agent_session': {'kind': 'id', 'value': 'abc'}}))
        collector = self.collector()
        calls = []
        def rpc(endpoint, method, params=None):
            calls.append((method, params))
            return {'agents': [good]} if method == 'agent.list' else {}
        with patch.object(info, 'rpc', rpc), patch.object(fallback, 'rpc', rpc):
            status = collector.sync()
        self.assertEqual(status['models'], 1)
        report = next(p for m, p in calls if m == 'pane.report_metadata' and p.get('source') == info.SOURCE)
        self.assertEqual(report['tokens']['agent_info'], 'pi m')


class UntrustedInputTests(Fixture):
    def test_values_of_the_wrong_type_are_ignored(self):
        # Lists and objects cannot be hashed, so each would raise if used in a set or dict lookup.
        entries = [
            {'type': ['message'], 'id': 'odd-type', 'parentId': None},
            {'type': 'model_change', 'id': 'a', 'parentId': None, 'provider': ['p'], 'modelId': 'm'},
            {'type': 'thinking_level_change', 'id': 'b', 'parentId': 'a', 'thinkingLevel': ['high']},
            reply({'p': 1}, 'm', parent='b', id_='c', stop=['x'], totalTokens=10),
            {'type': 'message', 'id': 'd', 'parentId': ['c'], 'message': {'role': 'user'}},
            {'type': {'k': 'v'}, 'id': 'e', 'parentId': {'k': 'v'}},
        ]
        observation = self.observe('pi', self.session('pi', entries))
        # The leaf has an unusable parent, so the branch is that entry alone.
        self.assertEqual(observation, {})
        entries = entries[1:4]
        observation = self.observe('pi', self.session('pi', entries))
        self.assertEqual(observation, {'model_id': 'm', 'model': 'm', 'context': {'used': 10}})
        self.assertIsNone(pi.state('pi', [pi.slim(e) for e in entries])[0])

    def test_json_nested_too_deeply_is_skipped(self):
        path = self.session('pi', [])
        line = json.dumps({'type': 'model_change', 'id': 'a', 'parentId': None, 'provider': 'p', 'modelId': 'm'})
        path.write_text(TOO_DEEP + '\n' + line + '\n')
        self.assertEqual(self.observe('pi', path)['model_id'], 'm')
        models = self.home / '.pi/agent/models.json'
        models.write_text(TOO_DEEP)
        self.assertEqual(pi.read_json(models), {})
        self.assertEqual(self.observe('pi', path)['model_id'], 'm')

    def test_catalog_nested_too_deeply_is_empty(self):
        package = self.home / 'pi-coding-agent'
        data = package / 'node_modules/@earendil-works/pi-ai/dist/providers/data'
        data.mkdir(parents=True)
        (data / 'deep.json').write_text(TOO_DEEP)
        with patch.object(pi, 'package_dir', return_value=package):
            self.assertEqual(pi.Catalog('pi').lookup('p', 'm'), (None, None))

    def test_catalog_skips_entries_that_are_not_objects(self):
        package = self.home / 'pi-coding-agent'
        data = package / 'node_modules/@earendil-works/pi-ai/dist/providers/data'
        data.mkdir(parents=True)
        (data / 'mixed.json').write_text(json.dumps({'api': {
            'bad': ['not', 'an', 'object'], 'odd': {'id': 'x', 'provider': ['p']},
            'good': {'id': 'm', 'provider': 'p', 'contextWindow': 1000}}}))
        with patch.object(pi, 'package_dir', return_value=package):
            self.assertEqual(pi.Catalog('pi').lookup('p', 'm'), (1000, None))

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'needs named pipes')
    def test_a_fifo_in_place_of_the_session_file_is_refused(self):
        path = self.session('pi', chain({'type': 'model_change', 'provider': 'p', 'modelId': 'm', 'parentId': None}))
        reader = pi.Reader()
        self.assertEqual(reader.observe('pi', path)['model_id'], 'm')
        # Replaced after validation: opening it for reading must not wait for a writer.
        path.unlink()
        os.mkfifo(path)
        self.assertEqual(reader.observe('pi', path), {})
        with self.assertRaises(OSError):
            pi.SessionFile(path).update()
        self.assertIsNone(pi.allowed_path('pi', str(path)))


class YamlTests(unittest.TestCase):
    def test_block_style_subset(self):
        text = ('# models\nproviders:\n  a:\n    models:\n      - id: "x:1"\n        contextWindow: 1000\n'
                '      - id: y\n    modelOverrides:\n      z:\n        name: Zed\n  b:\n    baseUrl: http://h:1/v1\n')
        self.assertEqual(pi.simple_yaml(text), {'providers': {
            'a': {'models': [{'id': 'x:1', 'contextWindow': 1000}, {'id': 'y'}],
                  'modelOverrides': {'z': {'name': 'Zed'}}},
            'b': {'baseUrl': 'http://h:1/v1'}}})
        self.assertEqual(pi.simple_yaml('[1, 2]\n'), {})


if __name__ == '__main__':
    unittest.main()
