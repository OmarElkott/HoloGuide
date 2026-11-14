"""Offline self-test: local endpoint, model on disk, no internet route."""
import io
import json

from src.dashboard.offline_check import run_check


def tags(*names):
    def opener(url, timeout):
        assert url == 'http://localhost:11434/api/tags'
        return io.BytesIO(json.dumps({'models': [{'name': n} for n in names]}).encode())
    return opener


def no_route(address, timeout):
    raise OSError('Network is unreachable')


class Sock:
    def close(self):
        pass


def test_offline_verified_when_local_model_present_and_no_route():
    report = run_check('http://localhost:11434', 'gemma3:4b', connect=no_route, opener=tags('gemma3:4b'))
    assert report.verdict == 'OFFLINE VERIFIED'


def test_network_up_is_reported_honestly():
    report = run_check('http://127.0.0.1:11434', 'gemma3:4b', connect=lambda a, timeout: Sock(),
                       opener=lambda url, timeout: tags('gemma3:4b')('http://localhost:11434/api/tags', timeout))
    assert report.verdict == 'LOCAL AI - NETWORK STILL UP' and report.internet_reachable


def test_missing_model_is_not_ready_and_remote_endpoint_is_never_queried():
    assert run_check('http://localhost:11434', 'gemma3:4b', connect=no_route,
                     opener=tags('other:1b')).verdict == 'AI NOT READY'
    def forbidden(url, timeout):
        raise AssertionError('remote endpoint must not be queried')
    report = run_check('http://10.0.0.5:11434', 'gemma3:4b', connect=no_route, opener=forbidden)
    assert report.verdict == 'AI NOT READY' and not report.endpoint_local
