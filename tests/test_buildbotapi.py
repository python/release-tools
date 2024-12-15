from functools import cache
from unittest.mock import AsyncMock

import aiohttp
import pytest

import buildbotapi


def test_builder_class() -> None:
    # Arrange / Act
    builder = buildbotapi.Builder(
        builderid=123,
        description="my description",
        name="my name",
        tags=["tag1", "tag2"],
    )

    # Assert
    assert builder.builderid == 123
    assert builder.description == "my description"
    assert builder.name == "my name"
    assert builder.tags == ["tag1", "tag2"]
    assert hash(builder) == 123


@cache
def load(filename: str) -> str:
    with open(filename) as f:
        return f.read()


@pytest.mark.asyncio
async def test_buildbotapi_authenticate() -> None:
    # Arrange
    async with AsyncMock(aiohttp.ClientSession) as mock_session:
        api = buildbotapi.BuildBotAPI(mock_session)

        # Act
        await api.authenticate(token="")

    # Assert
    mock_session.get.assert_called_with(
        "https://buildbot.python.org/all/auth/login", params={"token": ""}
    )


@pytest.mark.asyncio
async def test_buildbotapi_all_builders() -> None:
    # Arrange
    mock_session = AsyncMock(aiohttp.ClientSession)
    mock_session.get.return_value.__aenter__.return_value.status = 200
    mock_session.get.return_value.__aenter__.return_value.text.return_value = load(
        "tests/buildbotapi/builders.json"
    )
    api = buildbotapi.BuildBotAPI(mock_session)

    # Act
    all_builders = await api.all_builders()

    # Assert
    mock_session.get.assert_called_with(
        "https://buildbot.python.org/all/api/v2/builders"
    )
    assert len(all_builders) == 2
    assert all_builders[3].name == "AMD64 RHEL8 LTO 3.13"
    assert all_builders[1623].name == "AMD64 Windows PGO NoGIL PR"


@pytest.mark.asyncio
async def test_buildbotapi_all_builders_with_branch() -> None:
    # Arrange
    mock_session = AsyncMock(aiohttp.ClientSession)
    mock_session.get.return_value.__aenter__.return_value.status = 200
    mock_session.get.return_value.__aenter__.return_value.text.return_value = load(
        "tests/buildbotapi/builders.json"
    )
    api = buildbotapi.BuildBotAPI(mock_session)

    # Act
    await api.all_builders(branch="3.13")

    # Assert
    mock_session.get.assert_called_with(
        "https://buildbot.python.org/all/api/v2/builders?tags__contains=3.13"
    )


@pytest.mark.asyncio
async def test_buildbotapi_stable_builders() -> None:
    # Arrange
    mock_session = AsyncMock(aiohttp.ClientSession)
    mock_session.get.return_value.__aenter__.return_value.status = 200
    mock_session.get.return_value.__aenter__.return_value.text.return_value = load(
        "tests/buildbotapi/builders.json"
    )
    api = buildbotapi.BuildBotAPI(mock_session)

    # Act
    all_builders = await api.stable_builders()

    # Assert
    mock_session.get.assert_called_with(
        "https://buildbot.python.org/all/api/v2/builders"
    )
    assert len(all_builders) == 1
    assert all_builders[3].name == "AMD64 RHEL8 LTO 3.13"
    assert "stable" in all_builders[3].tags


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ["json_data", "expected"],
    [
        ("tests/buildbotapi/success.json", False),
        ("tests/buildbotapi/failure.json", True),
        ("tests/buildbotapi/no-builds.json", False),
    ],
)
async def test_buildbotapi_is_builder_failing_currently_yes(
    json_data: str, expected: bool
) -> None:
    # Arrange
    mock_session = AsyncMock(aiohttp.ClientSession)
    mock_session.get.return_value.__aenter__.return_value.status = 200
    mock_session.get.return_value.__aenter__.return_value.text.return_value = load(
        json_data
    )
    api = buildbotapi.BuildBotAPI(mock_session)
    builder = buildbotapi.Builder(builderid=3)

    # Act
    failing = await api.is_builder_failing_currently(builder=builder)

    # Assert
    mock_session.get.assert_called_with(
        "https://buildbot.python.org/all/api/v2/builds?complete__eq=true"
        "&&builderid__eq=3&&order=-complete_at&&limit=1"
    )
    assert failing is expected
