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


# The most recent builds in success.json and failure.json
SUCCESS_COMPLETE_AT = 1728312495
FAILURE_COMPLETE_AT = 1734198808
DAY = buildbotapi.SECONDS_PER_DAY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ["json_data", "now", "expected"],
    [
        # Recent builds: judged on their result
        ("tests/buildbotapi/success.json", SUCCESS_COMPLETE_AT + DAY, False),
        ("tests/buildbotapi/failure.json", FAILURE_COMPLETE_AT + DAY, True),
        ("tests/buildbotapi/no-builds.json", FAILURE_COMPLETE_AT + DAY, False),
        # Just inside the staleness cutoff: failure still counts
        ("tests/buildbotapi/failure.json", FAILURE_COMPLETE_AT + 13 * DAY, True),
        # Stale build (last run > 14 days ago): builder ignored
        ("tests/buildbotapi/failure.json", FAILURE_COMPLETE_AT + 15 * DAY, False),
    ],
)
async def test_buildbotapi_is_builder_failing_currently(
    monkeypatch: pytest.MonkeyPatch, json_data: str, now: int, expected: bool
) -> None:
    # Arrange
    mock_session = AsyncMock(aiohttp.ClientSession)
    mock_session.get.return_value.__aenter__.return_value.status = 200
    mock_session.get.return_value.__aenter__.return_value.text.return_value = load(
        json_data
    )
    monkeypatch.setattr("buildbotapi.time.time", lambda: now)
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
