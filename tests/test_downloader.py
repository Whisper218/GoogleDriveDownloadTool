import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import download_drive as app

RCLONE = app.BASE / 'tools' / 'rclone.exe'


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.base = Path(self.temp.name).resolve()
        self.patches = [patch.object(app, key, value) for key, value in {
            'BASE': self.base, 'DOWNLOAD': self.base / 'download',
            'CONTROL': self.base / 'download_state',
            'STATE': self.base / 'download_state' / 'progress.json'}.items()]
        for item in self.patches:
            item.start()
        self.cfg = {'batch_size': 2, 'remote': 'test', 'folder_id': app.FOLDER,
                    'attempts': 2, 'retry_delay_seconds': 0}
        self.names = ['a.tif', 'b.tif', 'c.tif']
        self.calls = []

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def copy(self, exe, cfg, name, target):
        self.calls.append(name)
        target.write_bytes(name.encode())
        return {'size': target.stat().st_size, 'sha256': app.digest(target)}

    def run_batch(self, state, answers=(), copier=None):
        iterator = iter(answers)
        with contextlib.redirect_stdout(io.StringIO()):
            return app.run_batches(self.cfg, state, 'fake', copier or self.copy,
                                   lambda prompt: next(iterator, ''), lambda sec: None)

    def test_no_confirmation_never_deletes_or_advances_and_restart_skips_download(self):
        state = app.load_state(self.cfg, self.names)
        self.run_batch(state)
        folder = app.batch_directory(state)
        self.assertEqual(self.calls, self.names[:2])
        self.assertTrue((folder / 'a.tif').exists())
        self.assertEqual(state['phase'], 'awaiting_confirmation')
        state = app.load_state(self.cfg, self.names)
        self.run_batch(state, ['yes'])
        self.assertEqual(self.calls, self.names[:2])
        self.assertTrue((folder / 'a.tif').exists())
        self.assertEqual(state['batch_index'], 0)

    def test_no_confirmation_never_deletes_or_advances_and_restart_skips_download(self):
        state = app.load_state(self.cfg, self.names)
        self.run_batch(state)
        folder = app.batch_directory(state)
        self.assertEqual(self.calls, self.names[:2])
        self.assertTrue((folder / 'a.tif').exists())
        self.assertEqual(state['phase'], 'awaiting_confirmation')
        state = app.load_state(self.cfg, self.names)
        self.run_batch(state, ['yes'])
        self.assertEqual(self.calls, self.names[:2])
        self.assertTrue((folder / 'a.tif').exists())
        self.assertEqual(state['batch_index'], 0)

    def test_confirmation_preserves_batches_and_advances(self):
        state = app.load_state(self.cfg, self.names)
        self.run_batch(state)
        old = app.batch_directory(state)
        unrelated = app.DOWNLOAD / 'keep.txt'
        unrelated.write_text('preserve')
        self.run_batch(state, ['DELETE 1', 'q'])
        self.assertTrue((old / 'a.tif').exists())
        self.assertTrue(unrelated.exists())
        next_folder = app.DOWNLOAD / 'batch_0002'
        self.assertTrue((next_folder / 'c.tif').exists())
        self.assertEqual(self.calls, self.names)
        self.assertEqual(state['phase'], 'awaiting_confirmation')
        self.run_batch(state, ['DELETE 2'])
        self.assertEqual(state['phase'], 'complete')
        self.run_batch(app.load_state(self.cfg, self.names))
        self.assertEqual(self.calls, self.names)

    def test_failed_file_retries_does_not_advance_and_recovery_only_downloads_failure(self):
        state = app.load_state(self.cfg, self.names)
        def failing(exe, cfg, name, target):
            if name == 'b.tif':
                self.calls.append(name)
                raise RuntimeError('simulated network error')
            return self.copy(exe, cfg, name, target)
        self.assertEqual(self.run_batch(state, ['q'], failing), 2)
        self.assertEqual(self.calls, ['a.tif', 'b.tif', 'b.tif'])
        self.assertEqual(state['phase'], 'downloading')
        self.assertEqual(state['failures']['b.tif']['attempts'], 2)
        self.assertIn('simulated network error', (app.CONTROL / 'failed_files.csv').read_text('utf-8-sig'))
        self.run_batch(app.load_state(self.cfg, self.names), ['r', 'q'])
        self.assertEqual(self.calls, ['a.tif', 'b.tif', 'b.tif', 'b.tif'])

    def test_interrupt_preserves_completed_file_and_resumes_in_order(self):
        state = app.load_state(self.cfg, self.names)
        def interrupted(exe, cfg, name, target):
            if name == 'b.tif':
                raise KeyboardInterrupt()
            return self.copy(exe, cfg, name, target)
        with self.assertRaises(KeyboardInterrupt):
            self.run_batch(state, copier=interrupted)
        saved = app.load_state(self.cfg, self.names)
        self.assertEqual(list(saved['downloaded']), ['a.tif'])
        self.run_batch(saved)
        self.assertEqual(self.calls, ['a.tif', 'b.tif'])

    def test_cleanup_crash_requires_another_confirmation(self):
        state = app.load_state(self.cfg, self.names)
        self.run_batch(state)
        folder = app.batch_directory(state)
        state['phase'] = 'cleanup_pending'
        app.persist(state)
        (folder / 'a.tif').unlink()
        self.run_batch(app.load_state(self.cfg, self.names), ['q'])
        self.assertTrue((folder / 'b.tif').exists())
        self.run_batch(app.load_state(self.cfg, self.names), ['DELETE 1', 'q'])
        self.assertTrue((folder / 'b.tif').exists())

    def test_cleanup_confirmation_preserves_batch_files(self):
        state = app.load_state(self.cfg, self.names)
        self.run_batch(state)
        folder = app.batch_directory(state)
        self.run_batch(app.load_state(self.cfg, self.names), ['DELETE 1', 'q'])
        self.assertTrue((folder / 'a.tif').exists())

    def test_remote_additions_preserve_legacy_snapshot_and_owner(self):
        state = app.new_state(self.cfg, self.names)
        state['plan_id'] = app.legacy_plan_id(self.cfg, self.names, 2)
        app.persist(state)
        folder = app.batch_directory(state)
        before = app.STATE.read_bytes()
        loaded = app.load_state(dict(self.cfg, batch_size=500), ['c.tif', 'new.tif', 'b.tif', 'a.tif'])
        self.assertEqual(loaded['files'], self.names)
        self.assertEqual(app.batch_directory(loaded), folder)
        self.assertEqual(app.STATE.read_bytes(), before)
        with self.assertRaises(ValueError):
            app.load_state(self.cfg, ['a.tif', 'b.tif'])
        with self.assertRaises(ValueError):
            app.load_state(dict(self.cfg, folder_id='other'), self.names)

    def test_config_size_applies_to_next_batch(self):
        state = app.load_state(self.cfg, self.names)
        state['preferred_batch_size'] = 2
        cfg = dict(self.cfg, batch_size=500)
        app.select_batch_size(state, cfg, lambda prompt: '')
        self.assertEqual(state['preferred_batch_size'], 500)

    def test_legacy_plan_accepts_batch_size_change(self):
        state = app.load_state(self.cfg, self.names)
        state['plan_id'] = app.legacy_plan_id(self.cfg, self.names, 2)
        app.persist(state)
        loaded = app.load_state(dict(self.cfg, batch_size=500), self.names)
        self.assertEqual(loaded['files'], self.names)

    def test_batch_size_config_can_change_without_changing_source_plan(self):
        state = app.load_state(self.cfg, self.names)
        state['batch_ranges'] = [[0, 2], [2, 3]]
        state['batch_size_selected_for'] = 0
        app.persist(state)
        loaded = app.load_state(dict(self.cfg, batch_size=500), self.names)
        self.assertEqual(app.batch_bounds(loaded), [0, 2])

    def test_missing_config_falls_back_to_example(self):
        example = dict(self.cfg, proxy='', excel=None, csv=None, folder_id='REPLACE_WITH_FOLDER_ID',
                       attempts=3, retry_delay_seconds=10)
        (self.base / 'config.example.json').write_text(json.dumps(example), encoding='utf-8')
        args = argparse.Namespace(config='config.json', batch_size=None, remote=None)
        loaded = app.load_config(args)
        self.assertEqual(loaded['remote'], 'test')
        self.assertIsNone(app.read_names(loaded))

    def test_changed_plan_rejected(self):
        self.cfg['excel'] = 'source.xlsx'
        app.load_state(self.cfg, self.names)
        with self.assertRaises(ValueError):
            app.load_state(self.cfg, list(reversed(self.names)))
        loaded = app.load_state(dict(self.cfg, batch_size=1), self.names)
        self.assertEqual(loaded['batch_size'], 2)

    def test_interactive_size_changes_preserve_order_and_resume(self):
        state = app.load_state(self.cfg, self.names)
        self.cfg['interactive_batch_size'] = True
        self.run_batch(state, ['0', '-1', 'abc', '1', 'DELETE 1', '2', 'q'])
        self.assertEqual(self.calls, self.names)
        self.assertEqual(state['batch_ranges'], [[0, 1], [1, 3]])
        self.assertEqual(state['history'][0]['count'], 1)
        self.assertEqual(state['batch_index'], 1)
        loaded = app.load_state(dict(self.cfg, batch_size=100), self.names)
        self.run_batch(loaded, ['DELETE 2'])
        self.assertEqual(loaded['phase'], 'complete')
        self.assertEqual(self.calls, self.names)

    def test_in_progress_range_is_not_resized(self):
        state = app.load_state(self.cfg, self.names)
        folder = app.batch_directory(state)
        state['downloaded']['a.tif'] = self.copy(None, None, 'a.tif', folder / 'a.tif')
        app.persist(state)
        self.cfg.update(batch_size=1, interactive_batch_size=True)
        self.run_batch(state, ['q'])
        self.assertEqual(self.calls, ['a.tif', 'b.tif'])
        self.assertEqual(app.batch_bounds(state), [0, 2])

    def test_size_selection_is_saved_before_first_transfer(self):
        state = app.load_state(self.cfg, self.names)
        cfg = dict(self.cfg, interactive_batch_size=True)
        self.assertTrue(app.select_batch_size(state, cfg, lambda prompt: '1'))
        loaded = app.load_state(self.cfg, self.names)
        self.assertTrue(app.select_batch_size(loaded, cfg, lambda prompt: self.fail('must not prompt again')))
        self.assertEqual(app.batch_bounds(loaded), [0, 1])

    def test_batch_size_over_100_downloads_remaining_and_resumes(self):
        self.names = [f'file_{n}.tif' for n in range(150)]
        state = app.load_state(self.cfg, self.names)
        self.cfg['interactive_batch_size'] = True
        self.run_batch(state, ['500', 'q'])
        self.assertEqual(self.calls, self.names)
        self.assertEqual(state['batch_ranges'], [[0, 150]])
        self.assertEqual(state['preferred_batch_size'], 500)
        loaded = app.load_state(self.cfg, self.names)
        self.run_batch(loaded, ['DELETE 1'])
        self.assertEqual(loaded['phase'], 'complete')
        self.assertEqual(len(self.calls), 150)

    def test_invalid_saved_ranges_rejected(self):
        state = app.load_state(self.cfg, self.names)
        state['batch_ranges'] = [[0, 1], [2, 3]]
        app.persist(state)
        with self.assertRaises(ValueError):
            app.load_state(self.cfg, self.names)

    def test_modified_download_is_preserved_and_not_overwritten(self):
        state = app.load_state(self.cfg, self.names)
        folder = app.batch_directory(state)
        info = self.copy(None, None, 'a.tif', folder / 'a.tif')
        state['downloaded']['a.tif'] = info
        app.persist(state)
        (folder / 'a.tif').write_text('changed')
        with self.assertRaises(RuntimeError):
            self.run_batch(state)
        self.assertEqual((folder / 'a.tif').read_text(), 'changed')

    def test_unsafe_names_rejected(self):
        for name in ['../bad.tif', 'a/b.tif', 'C:\\bad', 'NUL.tif', 'a:tif', '.batch-owner.json']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                app.safe_name(name)

    def test_csv_and_url_inputs(self):
        path = self.base / 'source.csv'
        path.write_text('Missing_File,Other\nz.tif,9\n,\na.tif,1\n', encoding='utf-8-sig')
        self.assertEqual(app.read_csv({'csv': str(path), 'column': 'Missing_File'}), ['z.tif', 'a.tif'])
        self.assertEqual(app.folder_id_from_url('https://drive.google.com/drive/u/0/folders/abc_123?usp=sharing'), 'abc_123')
        with self.assertRaises(ValueError):
            app.folder_id_from_url('https://example.com/folders/abc')

    def test_wrong_confirmation_cannot_delete(self):
        state = app.load_state(self.cfg, self.names)
        folder = app.batch_directory(state)
        with self.assertRaises(ValueError):
            app.clean_batch(state, 'DELETE 2')
        self.assertTrue(folder.exists())

    def test_double_start_rejected(self):
        with app.exclusive_lock():
            with self.assertRaises(RuntimeError):
                with app.exclusive_lock():
                    pass

    def test_excel_order_blank_rows_and_duplicates(self):
        from openpyxl import Workbook
        book = Workbook()
        sheet = book.active
        sheet.append(['Missing_Number', 'Missing_File'])
        sheet.append([9, 'z.tif'])
        sheet.append([None, None])
        sheet.append([1, 'a.tif'])
        path = self.base / 'source.xlsx'
        book.save(path)
        cfg = {'excel': str(path), 'column': 'Missing_File'}
        self.assertEqual(app.read_excel(cfg), ['z.tif', 'a.tif'])
        sheet.append([10, 'Z.TIF'])
        book.save(path)
        with self.assertRaises(ValueError):
            app.read_excel(cfg)

    def test_existing_untracked_downloads_rejected(self):
        app.DOWNLOAD.mkdir()
        (app.DOWNLOAD / 'precious.tif').touch()
        with self.assertRaises(ValueError):
            app.load_state(self.cfg, self.names)

    def test_deferred_failures_survive_advancement_and_require_cleanup_confirmation(self):
        state = app.load_state(self.cfg, self.names)
        def failing(exe, cfg, name, target):
            if name == 'b.tif':
                raise RuntimeError('missing source')
            return self.copy(exe, cfg, name, target)
        self.run_batch(state, ['q'], failing)
        folder = app.batch_directory(state)
        app.defer_failures(state)
        self.assertEqual(state['phase'], 'awaiting_confirmation')
        self.run_batch(state, ['q'])
        self.assertTrue((folder / 'a.tif').exists())
        self.assertEqual(state['batch_index'], 0)
        self.run_batch(app.load_state(self.cfg, self.names), ['DELETE 1', 'q'])
        self.assertEqual(self.calls, ['a.tif', 'c.tif'])
        self.assertEqual(state['history'], [])  # Original snapshot was not advanced.
        saved = app.load_state(self.cfg, self.names)
        self.assertEqual(saved['history'][0]['deferred_count'], 1)
        self.assertEqual(saved['history'][0]['downloaded_count'], 1)
        archives = list((app.CONTROL / 'deferred_failures').glob('*.json'))
        self.assertEqual(len(archives), 1)
        self.assertIn('b.tif', json.loads(archives[0].read_text('utf-8'))['failures'])
        self.assertTrue(archives[0].with_suffix('.csv').is_file())

    def test_cannot_defer_unattempted_files(self):
        state = app.load_state(self.cfg, self.names)
        app.batch_directory(state)
        state['failures']['a.tif'] = {'attempts': 1, 'error': 'missing', 'time': 'now'}
        with self.assertRaises(ValueError):
            app.defer_failures(state)
        self.assertEqual(state['phase'], 'downloading')

    def test_failed_batch_continues_from_normal_entry_without_retry(self):
        state = app.load_state(self.cfg, self.names)
        def failing(exe, cfg, name, target):
            if name == 'b.tif':
                raise RuntimeError('missing source')
            return self.copy(exe, cfg, name, target)
        self.run_batch(state, ['q'], failing)
        self.run_batch(state, ['c', 'DELETE 1', 'q'])
        self.assertEqual(self.calls, ['a.tif', 'c.tif'])
        self.assertEqual(state['batch_index'], 1)
        self.assertEqual(state['history'][0]['deferred_count'], 1)

    def test_missing_uploaded_files_require_explicit_confirmation_to_continue(self):
        state = app.load_state(self.cfg, self.names)
        folder = app.batch_directory(state)
        state['downloaded']['a.tif'] = self.copy(None, None, 'a.tif', folder / 'a.tif')
        state['failures']['b.tif'] = {'attempts': 3, 'error': 'not found', 'time': 'now'}
        app.persist(state)
        (folder / 'a.tif').unlink()
        self.run_batch(state, ['c', 'q'])
        self.assertEqual(state['phase'], 'downloading')
        self.assertEqual(state['batch_index'], 0)
        self.run_batch(state, ['c', 'UPLOADED 1', 'q'])
        self.assertEqual(state['phase'], 'awaiting_confirmation')
        self.assertTrue(folder.exists())
        records = list((app.CONTROL / 'deferred_failures').glob('*.json'))
        self.assertTrue(json.loads(records[0].read_text('utf-8'))['external_upload_confirmed'])
        self.run_batch(state, ['DELETE 1', 'q'])
        self.assertEqual(state['batch_index'], 1)
        self.assertEqual(self.calls, ['a.tif', 'c.tif'])

    def test_project_proxy_applies_to_children_and_bypasses_local_callback(self):
        with patch.dict(os.environ, {}, clear=True):
            app.apply_proxy({'proxy': 'socks5://127.0.0.1:10808'})
            self.assertEqual(os.environ['HTTPS_PROXY'], 'socks5://127.0.0.1:10808')
            self.assertEqual(os.environ['HTTP_PROXY'], os.environ['HTTPS_PROXY'])
            self.assertIn('127.0.0.1', os.environ['NO_PROXY'])
            app.apply_proxy({'proxy': ''})
            self.assertEqual(os.environ['HTTPS_PROXY'], 'socks5://127.0.0.1:10808')
        with self.assertRaises(ValueError):
            app.apply_proxy({'proxy': 'bad value'})

    def test_email_named_remote_is_accepted(self):
        path = self.base / 'config.json'
        cfg = dict(self.cfg, remote='whisper.shi218@gmail.com')
        path.write_text(json.dumps(cfg), encoding='utf-8')
        args = argparse.Namespace(config=str(path), batch_size=None, remote=None)
        self.assertEqual(app.load_config(args)['remote'], 'whisper.shi218@gmail.com')

    @unittest.skipUnless(RCLONE.is_file(), 'rclone is not installed')
    def test_real_rclone_copy_checksums_and_directory_rejection(self):
        source = self.base / 'source'
        source.mkdir()
        data = b'local integration test\x00' * 2048
        (source / 'a.tif').write_bytes(data)
        (source / 'nested.tif').mkdir()
        destination = self.base / 'destination'
        destination.mkdir()
        app.CONTROL.mkdir()
        cfg = dict(self.cfg, buffer_size='8Mi', max_buffer_memory='32Mi', bandwidth_limit='off')
        # Environment-only local alias: no Google credentials, remote changes, or network.
        with patch.dict(os.environ, {'RCLONE_CONFIG_TEST_TYPE': 'alias',
                                     'RCLONE_CONFIG_TEST_REMOTE': str(source)}):
            app.preflight(str(RCLONE), cfg)
            with contextlib.redirect_stdout(io.StringIO()):
                info = app.copy_file(str(RCLONE), cfg, 'a.tif', destination / 'a.tif')
            self.assertEqual((destination / 'a.tif').read_bytes(), data)
            self.assertEqual(info['size'], len(data))
            self.assertEqual(info['sha256'], app.digest(source / 'a.tif'))
            with self.assertRaises(RuntimeError):
                app.copy_file(str(RCLONE), cfg, 'nested.tif', destination / 'nested.tif')
            self.assertFalse((destination / 'nested.tif').exists())
            self.assertEqual((source / 'a.tif').read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
