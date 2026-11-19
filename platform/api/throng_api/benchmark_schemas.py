"""A run contains a suite; a tooling version can have many runs."""

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
ReferenceLabel = Annotated[str, Field(max_length=200)]
FiniteNumber = Annotated[float, Field(allow_inf_nan=False, strict=True)]
Score = Annotated[FiniteNumber, Field(ge=0)]


class BenchmarkReferenceMachine(Input):
    model_config = ConfigDict(str_strip_whitespace=False)
    name: ReferenceLabel
    short_name: ReferenceLabel
    os_name: ReferenceLabel
    os_version: ReferenceLabel
    cpu_architecture: ReferenceLabel
    memory_bytes: int = Field(strict=True, gt=0, lt=2**64)
    cpu_cores: int = Field(strict=True, gt=0, lt=2**32)


class BenchmarkReferenceScore(Input):
    model_config = ConfigDict(str_strip_whitespace=False)
    benchmark: BenchmarkName
    score: Score


class BenchmarkReferenceRun(Input):
    started_at_unix_seconds: FiniteNumber
    start_temperature_c: FiniteNumber
    end_temperature_c: FiniteNumber
    warmup_seconds: Score
    results: list[BenchmarkReferenceScore] = Field(min_length=1, max_length=1024)


class BenchmarkAggregatedScores(Input):
    pi_equivalent: Score | None = None


class BenchmarkMeasurement(Input):
    model_config = ConfigDict(str_strip_whitespace=False)
    benchmark_name: BenchmarkName
    number: Score
    pi_equivalent: Score | None = None
    reference_machine: BenchmarkReferenceMachine | None = None
    reference_run: BenchmarkReferenceRun | None = None


class BenchmarkCreate(BenchmarkMeasurement):
    computer_id: UUID
    benchmarker_version: Version
    run_id: UUID | None = None
    aggregated_scores: BenchmarkAggregatedScores | None = None


class BenchmarkUpdate(Input):
    number: Score


class BenchmarkSuite(Input):
    model_config = ConfigDict(str_strip_whitespace=False)
    benchmarker_version: Version
    run_id: UUID | None = None
    aggregated_scores: BenchmarkAggregatedScores | None = None
    results: list[BenchmarkMeasurement] = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def unique_names(self):
        if len({row.benchmark_name for row in self.results}) != len(self.results):
            raise ValueError("Duplicate benchmark names")
        return self
