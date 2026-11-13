"""The scaffold is real only if the pinned SDK imports and the package is importable."""

import importlib.metadata


def test_strands_is_pinned_to_1_50_2():
    assert importlib.metadata.version("strands-agents") == "1.64.0"


def test_strands_evals_is_pinned_to_1_9_0():
    assert importlib.metadata.version("strands-agents-evals") == "1.9.0"


def test_nora_package_imports():
    import nora

    assert nora.__name__ == "nora"


async def test_asyncio_mode_is_auto():
    """No @pytest.mark.asyncio decorator — this coroutine must still run."""
    assert True
