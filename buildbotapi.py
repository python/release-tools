import json
from dataclasses import dataclass
from typing import Any, cast

from aiohttp.client import ClientSession

JSON = dict[str, Any]


@dataclass
class Builder:
    builderid: int
    description: str | None
    name: str
    tags: list[str]

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(**kwargs)

    def __hash__(self) -> int:
        return hash(self.builderid)


class BuildBotAPI:
    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def authenticate(self, token: str) -> None:
        await self._session.get(
            "https://buildbot.python.org/all/auth/login", params={"token": token}
        )

    async def _fetch_text(self, url: str) -> str:
        async with self._session.get(url) as resp:
            return await resp.text()

    async def _fetch_json(self, url: str) -> JSON:
        return cast(JSON, json.loads(await self._fetch_text(url)))

    async def stable_builders(self, branch: str | None = None) -> dict[int, Builder]:
        stable_builders = {
            id: builder
            for (id, builder) in (await self.all_builders(branch=branch)).items()
            if "stable" in builder.tags
        }
        return stable_builders

    async def all_builders(self, branch: str | None = None) -> dict[int, Builder]:
        url = "https://buildbot.python.org/all/api/v2/builders"
        if branch is not None:
            url = f"{url}?tags__contains={branch}"
        _builders: dict[str, Any] = await self._fetch_json(url)
        builders = _builders["builders"]
        all_builders = {
            builder["builderid"]: Builder(**builder) for builder in builders
        }
        return all_builders

    async def is_builder_failing_currently(self, builder: Builder) -> bool:
        builds_: dict[str, Any] = await self._fetch_json(
            f"https://buildbot.python.org/all/api/v2/builds?complete__eq=true"
            f"&&builderid__eq={builder.builderid}&&order=-complete_at"
            f"&&limit=1"
        )
        builds = builds_["builds"]
        if not builds:
            return False
        (build,) = builds
        if build["results"] == 2:
            return True
        return False
