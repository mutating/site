"""The benchmark identity includes the name: a version contains an entire suite."""

from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, ConfigDict, Field, model_validator
from semver import Version as SemVer

from throng_api.schemas import Input


def semver_key(version: str) -> tuple[SemVer, str]:
    """SemVer 2 precedence; build metadata breaks ties deterministically only."""
    return SemVer.parse(version), version


def valid_version(value: str) -> str:
    SemVer.parse(value)
    return value


Version = Annotated[str, Field(min_length=5, max_length=200), AfterValidator(valid_version)]
BenchmarkName = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[a-z][a-z0-9_]*$")]
Score = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]


class BenchmarkMeasurement(Input):
    model_config = ConfigDict(str_strip_whitespace=False)
    benchmark_name: BenchmarkName
    number: Score


class BenchmarkCreate(BenchmarkMeasurement):
    computer_id: UUID
    benchmarker_version: Version


class BenchmarkUpdate(Input):
    number: Score


class BenchmarkSuite(Input):
    model_config = ConfigDict(str_strip_whitespace=False)
    benchmarker_version: Version
    results: list[BenchmarkMeasurement] = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def unique_names(self):
        if len({row.benchmark_name for row in self.results}) != len(self.results):
            raise ValueError("Duplicate benchmark names")
        return self
