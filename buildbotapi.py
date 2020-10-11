import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from aiohttp.client import ClientSession


@dataclass
class Builder:
    id: int
    builderid: int
    description: Optional[str]
    masterids: List[int]
    name: str
    tags: List[str]

    def __init__(self, **kwargs) -> None:
        self.__dict__.update(**kwargs)

    def __hash__(self) -> int:
        return hash(self.builderid)


@dataclass
class Build:
    id: int
    is_currently_failing: bool

    def __init__(self, **kwargs):
        self.__dict__.update(**kwargs)
        self.id = kwargs.get("number")
        self.is_currently_failing = kwargs.get("currently_failing")
        self.builder = None

    def __eq__(self, other):
        return self.id == other.id

    def __hash__(self):
        return hash(self.id)


class BuildBotAPI:
    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self._stable_builders: Optional[Dict[int, Builder]] = None

    async def authenticate(self, token: str) -> None:
        await self._session.get(
            "https://buildbot.python.org/all/auth/login", params={"token": token}
        )

    async def _fetch_text(self, url: str) -> str:
        async with self._session.get(url) as resp:
            return await resp.text()

    async def _fetch_json(
        self, url: str
    ) -> Dict[
        str,
        Union[
            List[Dict[str, Union[int, bool, str]]],
            Dict[str, int],
            List[Dict[str, Optional[Union[int, List[int], str, List[str]]]]],
        ],
    ]:
        return json.loads(await self._fetch_text(url))

    async def _get_stable_builders(self, branch: Optional[str]) -> Dict[int, Builder]:
        stable_builders = {
            id: builder
            for (id, builder) in (await self.all_builders(branch=branch)).items()
            if "stable" in builder.tags
        }
        if self._stable_builders is None:
            self._stable_builders = stable_builders
        return stable_builders

    async def all_builders(self, branch: Optional[str] = None) -> Dict[int, Builder]:
        url = "https://buildbot.python.org/all/api/v2/builders"
        if branch is not None:
            url = f"{url}?tags__contains={branch}"
        _builders: Dict[str, Any] = await self._fetch_json(url)
        builders = _builders["builders"]
        all_builders = {
            builder["builderid"]: Builder(**builder) for builder in builders
        }
        return all_builders

    async def stable_builders(self, branch: Optional[str] = None) -> Dict[int, Builder]:
        stable_builders = self._stable_builders
        if stable_builders is None:
            stable_builders = await self._get_stable_builders(branch=branch)
        return stable_builders

    async def is_builder_failing_currently(self, builder: Builder) -> bool:
        builds_: Dict[str, Any] = await self._fetch_json(
            f"https://buildbot.python.org/all/api/v2/builds?complete__eq=true"
            f"&&builderid__eq={builder.id}&&order=-complete_at"
            f"&&limit=1"
        )
        builds = builds_["builds"]
        if not builds:
            return False
        (build,) = builds
        if build["results"] == 2:
            return True
        return False

    async def get_build(self, builder_id, build_id):
        data = await self._fetch_json(
            f"https://buildbot.python.org/all/api/v2/builders/{builder_id}"
            f"/builds/{build_id}"
        )
        (build_data,) = data["builds"]
        build = Build(**build_data)
        build.builder = (await self.all_builders())[build.builderid]
        build.is_currently_failing = await self.is_builder_failing_currently(
            build.builder
        )
        return build

    async def get_recent_failures(self, limit=100):
        data = await self._fetch_json(
            f"https://buildbot.python.org/all/api/v2/builds?"
            f"complete__eq=true&&results__eq=2&&"
            f"order=-complete_at&&limit={limit}"
        )

        stable_builders = await self.stable_builders()

        all_failures = {
            Build(**build)
            for build in data["builds"]
            if build["builderid"] in stable_builders
        }

        for failure in all_failures:
            failure.builder = stable_builders[failure.builderid]

        async def _get_missing_info(failure):
            failure.is_currently_failing = await self.is_builder_failing_currently(
                failure.builder
            )

        await asyncio.gather(*[_get_missing_info(failure) for failure in all_failures])

        return all_failures
