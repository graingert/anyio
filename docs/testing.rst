Testing with AnyIO
==================

AnyIO provides built-in support for testing your library or application in the form of a pytest_
plugin.

.. _pytest: https://docs.pytest.org/en/latest/

Creating asynchronous tests
---------------------------

To mark a coroutine function to be run via :func:`anyio.run`, simply add the ``@pytest.mark.anyio``
decorator::

    import pytest


    @pytest.mark.anyio
    async def test_something():
        pass

Asynchronous fixtures
---------------------

The plugin also supports coroutine functions as fixtures, for the purpose of setting up and tearing
down asynchronous services used for tests::

    import pytest


    @pytest.fixture
    async def server():
        server = await setup_server()
        yield server
        await server.shutdown()


    @pytest.mark.anyio
    async def test_server(server):
        result = await server.do_something()
        assert result == 'foo'

Any coroutine fixture that is activated by a test marked with ``@pytest.mark.anyio`` will be run
with the same backend as the test itself. Both plain coroutine functions and asynchronous generator
functions are supported in the same manner as pytest itself does with regular functions and
generator functions.

Fixtures can have any scope, from ``function`` to ``session``. If the scope is broader than
``function``, the same event loop will be used until the teardown of the fixture that first
requested the specific combination of a backend and its options. In practice this means that if you
only have function scoped async fixtures (or no async fixtures at all), each test gets its own
event loop. Then again, if you have a module scoped async fixture, every test and fixture in that
module will be run with the same event loop.

Specifying the backend to run on
--------------------------------

By default, all tests are run against the default backend (asyncio). The pytest plugin provides a
command line switch (``--anyio-backends``) for selecting which backend(s) to run your tests
against. By specifying a special value, ``all``, it will run against all available backends.

For example, to run your test suite against the curio and trio backends:

.. code-block:: bash

    pytest --anyio-backends=curio,trio

Behind the scenes, any function that uses the ``@pytest.mark.anyio`` marker gets parametrized by
the plugin to use the ``anyio_backend`` fixture. One alternative is to do this parametrization on
your own::

    @pytest.mark.parametrize('anyio_backend', ['asyncio'])
    async def test_on_asyncio_only(anyio_backend):
        ...

Or you can write a simple fixture by the same name that provides the back-end name::

    @pytest.fixture(params=['asyncio'])
    def anyio_backend(request):
        return request.param

Using AnyIO from regular tests
------------------------------

In rare cases, you may need to have tests that run against whatever backends you have chosen to
work with. For this, you can add the ``anyio_backend`` parameter to your test. It will be filled
in with the name of each of the selected backends in turn::

    def test_something(anyio_backend):
        assert anyio_backend in ('asyncio', 'curio', 'trio')
