from contextlib import contextmanager
from inspect import iscoroutinefunction
from typing import Dict, Any, Tuple

import pytest
from async_generator import isasyncgenfunction

from . import BACKENDS, open_test_runner, TestRunner

_runners: Dict[Tuple[str, str], TestRunner] = {}


@contextmanager
def get_runner(backend: str, options: Dict[str, Any]):
    key = backend, str(options)
    if key in _runners:
        yield _runners[key]
    else:
        with open_test_runner(backend, **options) as runner:
            _runners[key] = runner
            try:
                yield runner
            finally:
                del _runners[key]


def pytest_configure(config):
    config.addinivalue_line('markers', 'anyio: mark the (coroutine function) test to be run '
                                       'asynchronously via anyio.')


def pytest_addoption(parser):
    group = parser.getgroup('AnyIO')
    group.addoption(
        '--anyio-backends', action='store', dest='anyio_backends', default=BACKENDS[0],
        help='Comma separated list of backends to use for running asynchronous tests.',
    )


def pytest_fixture_setup(fixturedef, request):
    def wrapper(*args, **kwargs):
        backend_name = kwargs['anyio_backend_name']
        backend_options = kwargs.get('anyio_backend_options', {})
        if 'anyio_backend_name' in strip_argnames:
            del kwargs['anyio_backend_name']
        if 'anyio_backend_options' in strip_argnames:
            del kwargs['anyio_backend_options']

        with get_runner(backend_name, backend_options) as runner:
            if isasyncgenfunction(func):
                gen = func(*args, **kwargs)
                try:
                    value = runner.call(gen.asend, None)
                except StopAsyncIteration:
                    raise RuntimeError('Async generator did not yield')

                yield value

                try:
                    runner.call(gen.asend, None)
                except StopAsyncIteration:
                    pass
                else:
                    runner.call(gen.aclose)
                    raise RuntimeError('Async generator fixture did not stop')
            else:
                yield runner.call(func, *args, **kwargs)

    func = fixturedef.func
    if (isasyncgenfunction(func) or iscoroutinefunction(func)) and 'anyio' in request.keywords:
        strip_argnames = []
        for argname in ('anyio_backend_name', 'anyio_backend_options'):
            if argname not in fixturedef.argnames:
                fixturedef.argnames += (argname,)
                strip_argnames.append(argname)

        fixturedef.func = wrapper


@pytest.hookimpl(trylast=True)
def pytest_generate_tests(metafunc):
    def get_backends():
        backends = metafunc.config.getoption('anyio_backends')
        if isinstance(backends, str):
            backends = backends.replace(' ', '').split(',')
        if backends == ['all']:
            backends = BACKENDS

        return backends

    marker = metafunc.definition.get_closest_marker('anyio')
    if marker and 'anyio_backend' not in metafunc.fixturenames:
        metafunc.fixturenames.append('anyio_backend')
        metafunc.parametrize('anyio_backend', get_backends())
    elif 'anyio_backend' in metafunc.fixturenames:
        try:
            metafunc.parametrize('anyio_backend', get_backends())
        except ValueError:
            pass  # already using a fixture or has been explicit parametrized


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    def run_with_hypothesis(**kwargs):
        with get_runner(backend_name, backend_options) as runner:
            runner.call(original_func, **kwargs)

    try:
        backend = pyfuncitem.callspec.getparam('anyio_backend')
    except (AttributeError, ValueError):
        return None

    if backend:
        if isinstance(backend, str):
            backend_name, backend_options = backend, {}
        else:
            backend_name, backend_options = backend
            if not isinstance(backend, str) or not isinstance(backend_options, dict):
                raise TypeError('anyio_backend must be either a string or tuple of (string, dict)')

        if hasattr(pyfuncitem.obj, 'hypothesis'):
            # Wrap the inner test function unless it's already wrapped
            original_func = pyfuncitem.obj.hypothesis.inner_test
            if original_func.__qualname__ != run_with_hypothesis.__qualname__:
                if iscoroutinefunction(pyfuncitem.obj):
                    pyfuncitem.obj.hypothesis.inner_test = run_with_hypothesis

            return False

        if iscoroutinefunction(pyfuncitem.obj):
            funcargs = pyfuncitem.funcargs
            testargs = {arg: funcargs[arg] for arg in pyfuncitem._fixtureinfo.argnames}
            with get_runner(backend_name, backend_options) as runner:
                runner.call(pyfuncitem.obj, **testargs)

            return True


@pytest.fixture
def anyio_backend_name(anyio_backend) -> str:
    if isinstance(anyio_backend, str):
        return anyio_backend
    else:
        return anyio_backend[0]


@pytest.fixture
def anyio_backend_options(anyio_backend) -> Dict[str, Any]:
    if isinstance(anyio_backend, str):
        return {}
    else:
        return anyio_backend[1]
