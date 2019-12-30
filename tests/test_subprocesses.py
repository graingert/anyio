import platform
from subprocess import CalledProcessError
from textwrap import dedent

import pytest

from anyio import run_process, open_process
from anyio.wrappers import BufferedByteReader


@pytest.mark.parametrize('shell, command', [
    pytest.param(True, 'python -c "import sys; print(sys.stdin.read()[::-1])"', id='shell'),
    pytest.param(False, ['python', '-c', 'import sys; print(sys.stdin.read()[::-1])'], id='exec')
])
@pytest.mark.anyio
async def test_run_process(shell, command, anyio_backend):
    if anyio_backend == 'curio' and platform.python_implementation() == 'PyPy':
        pytest.skip('This test causes Curio to crash PyPy')

    process = await run_process(command, input=b'abc')
    assert process.returncode == 0
    assert process.stdout == b'cba\n'


@pytest.mark.anyio
async def test_run_process_checked(anyio_backend):
    if anyio_backend == 'curio' and platform.python_implementation() == 'PyPy':
        pytest.skip('This test causes Curio to crash PyPy')

    with pytest.raises(CalledProcessError) as exc:
        await run_process(['python', '-c',
                           'import sys; print("stderr-text", file=sys.stderr); '
                           'print("stdout-text"); sys.exit(1)'], check=True)

    assert exc.value.returncode == 1
    assert exc.value.stdout == b'stdout-text\n'
    assert exc.value.stderr == b'stderr-text\n'


@pytest.mark.anyio
async def test_terminate(tmp_path, anyio_backend):
    if anyio_backend == 'curio' and platform.python_implementation() == 'PyPy':
        pytest.skip('This test causes Curio to crash PyPy')

    script_path = tmp_path / 'script.py'
    script_path.write_text(dedent("""\
        import signal, sys, time

        def terminate(signum, frame):
            print('exited with SIGTERM', flush=True)
            sys.exit()

        signal.signal(signal.SIGTERM, terminate)
        print('ready', flush=True)
        time.sleep(5)
    """))
    process = await open_process(['python', str(script_path)])
    buffered_stdout = BufferedByteReader(process.stdout)
    line = await buffered_stdout.receive_until(b'\n', 100)
    assert line == b'ready'

    process.terminate()
    line = await buffered_stdout.receive_until(b'\n', 100)
    assert line == b'exited with SIGTERM'
    assert await process.wait() == 0
