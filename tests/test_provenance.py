"""Provenance must work for source archives and unavailable Git executables."""
import subprocess
from types import SimpleNamespace

import pytest

from src.evaluation.provenance import code_identity


def test_archive_identity_tracks_source_without_using_parent_git(tmp_path, monkeypatch):
    source = tmp_path / 'src'
    source.mkdir()
    code = source / 'app.py'
    code.write_bytes(b'value = 1\r\n')
    def unexpected_git(*args, **kwargs):
        pytest.fail('A ZIP export must not use enclosing Git history')
    monkeypatch.setattr(subprocess, 'run', unexpected_git)
    first = code_identity(tmp_path)
    assert first['code_revision'] is None
    assert first['working_tree_dirty'] is None
    assert first['revision_status'] == 'unavailable'
    assert len(first['source_sha256']) == 64
    code.write_bytes(b'value = 1\n')
    assert code_identity(tmp_path) == first
    (tmp_path / 'outputs').mkdir()
    (tmp_path / 'outputs' / 'run.txt').write_text('generated')
    assert code_identity(tmp_path) == first
    code.write_text('value = 2\n')
    assert code_identity(tmp_path)['source_sha256'] != first['source_sha256']


@pytest.mark.parametrize('failure', [FileNotFoundError('git'),
                                    subprocess.TimeoutExpired('git', 5)])
def test_missing_or_unresponsive_git_retains_source_identity(tmp_path, monkeypatch, failure):
    (tmp_path / '.git').mkdir()
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(subprocess, 'run', fail)
    identity = code_identity(tmp_path)
    assert identity['revision_status'] == 'unavailable'
    assert identity['code_revision'] is None
    assert len(identity['source_sha256']) == 64


def test_git_revision_and_dirty_state_are_preserved(tmp_path, monkeypatch):
    (tmp_path / '.git').write_text('gitdir: elsewhere')
    responses = iter([SimpleNamespace(returncode=0, stdout='a' * 40 + '\n'),
                      SimpleNamespace(returncode=0, stdout=' M src/app.py\n')])
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: next(responses))
    identity = code_identity(tmp_path)
    assert identity['revision_status'] == 'git'
    assert identity['code_revision'] == 'a' * 40
    assert identity['working_tree_dirty'] is True
