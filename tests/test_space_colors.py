import curses
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import space_colors as colors
import sidebar_settings as prefs


class SpaceColorTests(unittest.TestCase):
    def setUp(self):
        root = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(prefs,'preferences_path',return_value=Path(root)/'settings.json'))

    def test_random_choice_receives_only_medium_shades(self):
        with tempfile.TemporaryDirectory() as root, patch.object(colors.random,'SystemRandom') as rng:
            rng.return_value.choice.return_value='cyan_medium'
            result=colors.assignments(Path(root),[{'workspace_id':'new'}])
            rng.return_value.choice.assert_called_once_with([hue+'_medium' for hue,*_ in colors.HUES])
            self.assertEqual(result,{'new':'cyan_medium'})

    def test_triplets_stay_together_and_group_gutters_are_wider(self):
        for width in (18,32,36,59,80):
            grid=colors.cells(width)
            self.assertEqual(colors.columns_for(width)%3,0)
            for i in range(0,36,3):
                triplet=grid[i:i+3]
                self.assertEqual(len({row for _,row,_ in triplet}),1)
                self.assertEqual(triplet[1][2]-triplet[0][2],5)
                self.assertEqual(triplet[2][2]-triplet[1][2],5)
                if i+3<36 and grid[i+3][1]==triplet[2][1]:
                    self.assertEqual(grid[i+3][2]-triplet[2][2]-4,4)

    def test_new_spaces_choose_random_medium_and_survive_rename(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            spaces = [{"workspace_id": f"w{i}", "label": str(i)} for i in range(24)]
            original = colors.assignments(directory, spaces)
            self.assertTrue(all(v.endswith('_medium') for v in original.values()))
            spaces[0]["label"] = "Renamed"
            self.assertEqual(colors.assignments(directory,spaces),original)
            changed = colors.assignments(directory,spaces,("w0","blue_light"))
            self.assertEqual(changed["w0"],"blue_light")
            self.assertEqual(colors.assignments(directory,spaces),changed)
            with self.assertRaises(ValueError):
                colors.assignments(directory,spaces,("closed","blue_light"))
            self.assertEqual(colors.assignments(directory,spaces),changed)

    def test_corrupt_state_does_not_reset_preferences(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)
            path=directory / "space-colors.json"
            path.write_text('{broken')
            with self.assertRaises(ValueError):
                colors.assignments(directory,[])
            self.assertEqual(path.read_text(),'{broken')

    def test_collector_saves_palette_without_competing_with_animation_reports(self):
        spaces=[{'workspace_id':'w1','label':'Example'}]
        with tempfile.TemporaryDirectory() as root, patch.object(colors,'rpc',return_value={'workspaces':spaces}) as rpc:
            chosen=colors.sync('test',Path(root),change=('w1','blue_light'))
            self.assertEqual(chosen,{'w1':'blue_light'})
            self.assertEqual(json.loads((Path(root)/'space-colors.json').read_text()),chosen)
            rpc.assert_called_once_with('test','workspace.list')

    def test_popup_keeps_explicit_target(self):
        with patch.dict(os.environ,{'HERDR_PLUGIN_CONTEXT_JSON':'{"workspace_id":"w2"}',
                                   'HERDR_WORKSPACE_ID':'w1'}), patch.object(colors,'rpc') as rpc:
            colors.open_picker('socket')
            params=rpc.call_args.args[2]
            self.assertNotIn('workspace_id',params)
            self.assertEqual(params['env']['SIDEBAR_CUSTOMIZER_WORKSPACE'],'w2')

    def test_grid_hit_testing_and_config_palette_agree(self):
        import tomllib
        block=colors.style_block()
        config=tomllib.loads('[ui.sidebar.spaces]\nrows = [[\n'+block+'\n]]\n'
                            '[ui.sidebar.agents]\nrows = [[\n'+block+'\n]]\n')
        for width in [32,60,80]:
            for index,row,col in colors.cells(width):
                self.assertEqual(colors.clicked(width,col+3,row+1),index)
            self.assertIsNone(colors.clicked(width,0,0))
        for section in ['spaces','agents']:
            row=config['ui']['sidebar'][section]['rows'][0]
            styled={t['token']:t['fg'] for t in row if isinstance(t,dict)}
            self.assertLessEqual(len(row),16)
            for key,dark,medium,light in colors.HUES:
                self.assertIn(styled['$space_'+key],(dark,medium,light))

    def test_grid_fits_actual_popup_inner_size_and_square_swatches(self):
        grid=colors.cells(59)
        self.assertEqual(len(grid),36)
        self.assertLess(max(row for _,row,_ in grid)+2,24-3)
        self.assertTrue(all(col+4 < 59 for _,_,col in grid))
        self.assertEqual(len({row for _,row,_ in grid}),4)
        for index,row,col in grid:
            self.assertEqual(colors.clicked(59,col+3,row+1),index)
            self.assertIsNone(colors.clicked(59,col+4,row+1))

    def test_palette_is_36_distinct_terminal_colors_and_grouped_shades(self):
        self.assertEqual(len(colors.PALETTE),36)
        self.assertEqual(len({colors.nearest_color(c) for _,_,c in colors.PALETTE}),36)
        for hue,dark,medium,light in colors.HUES:
            dark_rgb=[int(dark[i:i+2],16) for i in (1,3,5)]
            light_rgb=[int(light[i:i+2],16) for i in (1,3,5)]
            medium_rgb=[int(medium[i:i+2],16) for i in (1,3,5)]
            self.assertGreater(sum(light_rgb),sum(medium_rgb))
            self.assertGreater(sum(medium_rgb),sum(dark_rgb))

    def test_pastel_palette_increases_in_rendered_luminance(self):
        def luminance(hex_value):
            rgb=[int(hex_value[i:i+2],16)/255 for i in (1,3,5)]
            linear=[c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in rgb]
            return sum(c*w for c,w in zip(linear,(.2126,.7152,.0722)))
        previous_light=['#ff8787','#ffaf5f','#ffff5f','#afff5f','#5fff87','#5fffd7',
                        '#5fd7ff','#5fafff','#afafff','#d787ff','#ff87ff','#ff5faf']
        for (hue,dark,medium,light),previous in zip(colors.HUES,previous_light):
            self.assertEqual(medium,previous)
            self.assertLess(luminance(dark),luminance(medium),hue)
            self.assertLess(luminance(medium),luminance(light),hue)
            for color in (dark,medium,light):
                index=colors.nearest_color(color)-16
                levels=(0,95,135,175,215,255)
                rendered='#{:02x}{:02x}{:02x}'.format(levels[index//36],levels[(index//6)%6],levels[index%6])
                self.assertEqual(rendered,color)  # Curses never substitutes a darker approximation.

    def test_migrates_old_palette_without_losing_saved_choices(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)
            (directory/'space-colors.json').write_text('{"w1":"mauve","w2":"blue"}')
            spaces=[{'workspace_id':'w1'},{'workspace_id':'w2'}]
            result=colors.assignments(directory,spaces,settings=dict(prefs.DEFAULTS))
            self.assertEqual(result,{'w1':'violet_light','w2':'blue_light'})

    def test_unknown_palette_names_are_rejected_without_rewriting(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)
            path=directory/'space-colors.json'
            path.write_text('{"w1":"plaid"}')
            with self.assertRaises(ValueError):
                colors.assignments(directory,[{'workspace_id':'w1'}],settings=dict(prefs.DEFAULTS))
            self.assertEqual(path.read_text(),'{"w1":"plaid"}')

    def test_sync_uses_supplied_loader_so_malformed_settings_still_assign(self):
        spaces=[{'workspace_id':'w1','label':'Example'}]
        with tempfile.TemporaryDirectory() as root, patch.object(colors,'rpc',return_value={'workspaces':spaces}):
            path=Path(root)/'settings.json'
            path.write_text(json.dumps({'default_hue':'teal','default_shade':'dark'}))
            reader=prefs.SettingsReader(path)
            reader.load()
            path.write_text(json.dumps({'speed':9}))
            with patch.object(prefs,'preferences_path',return_value=path):
                with self.assertRaises(ValueError):
                    colors.sync('test',Path(root))
                chosen=colors.sync('test',Path(root),load=reader.load)
            self.assertEqual(chosen,{'w1':'teal_dark'})
            self.assertIsNotNone(reader.error)

    def test_tab_cycles_current_space_order_without_changing_herdr_focus(self):
        spaces=[{'workspace_id':'w1','label':'One'},{'workspace_id':'w2','label':'Two'}]
        with tempfile.TemporaryDirectory() as root, patch.object(colors,'rpc',return_value={'workspaces':spaces}) as rpc:
            state=colors.PickerState('socket',Path(root),'w1')
            state.refresh(1)
            self.assertEqual(state.wid,'w2')
            self.assertEqual(state.position,'2/2')
            state.refresh(1)
            self.assertEqual(state.wid,'w1')
            state.refresh(-1)
            self.assertEqual(state.wid,'w2')
            for call in rpc.call_args_list:
                self.assertEqual(call.args[1],'workspace.list')
