"""Owner CRUD and atomic, device-authenticated suite upload."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, or_, select

from throng_api import outputs as out
from throng_api.benchmark_schemas import (
    BenchmarkCreate,
    BenchmarkSuite,
    BenchmarkUpdate,
    semver_key,
)
from throng_api.computers import computer_for_user
from throng_api.device_security import agent_device_proof
from throng_api.models import BenchmarkResult, Computer, ComputerOwnership, utcnow
from throng_api.security import (
    access_token_scheme,
    agent_auth,
    agent_credential_scheme,
    authenticate,
    owned_filter,
)
from throng_api.serialization import public

router = APIRouter(
    prefix="/v1", tags=["benchmark results"], dependencies=[Depends(access_token_scheme)]
)
agent_router = APIRouter(
    prefix="/agent/v1",
    tags=["agent protocol v1"],
    dependencies=[Depends(agent_credential_scheme), Depends(agent_device_proof)],
)


async def record_run(session, computer_id, version, measurements, run_id):
    # Clients keep this ID across transport retries and generate a new one for
    # each actual run. Legacy clients without an ID create a run per upload.
    run_id = str(run_id or uuid4())
    existing = list(
        await session.scalars(
            select(BenchmarkResult).where(
                BenchmarkResult.computer_id == computer_id,
                BenchmarkResult.run_id == run_id,
            )
        )
    )
    if any(row.benchmarker_version != version for row in existing):
        raise HTTPException(409, "Benchmark run already has a different tooling version")
    by_name = {row.benchmark_name: row for row in existing}
    recorded_at = existing[0].created_at if existing else utcnow()
    results = []
    for measurement in measurements:
        row = by_name.get(measurement.benchmark_name)
        if row is not None:
            if row.number != measurement.number:
                raise HTTPException(409, "Benchmark run already has a different result")
        else:
            row = BenchmarkResult(
                computer_id=computer_id,
                run_id=run_id,
                created_at=recorded_at,
                benchmark_name=measurement.benchmark_name,
                benchmarker_version=version,
                number=measurement.number,
            )
            session.add(row)
        await session.flush()
        results.append(public(row))
    return results


@router.post("/benchmark-results", status_code=201, response_model=out.BenchmarkResult)
async def create_result(data: BenchmarkCreate, request: Request):
    async with request.app.state.db.write() as session:
        principal = await authenticate(session, request, "manage")
        computer = await computer_for_user(session, principal, data.computer_id, admin=True)
        return (
            await record_run(session, computer.id, data.benchmarker_version, [data], data.run_id)
        )[0]


async def results_for_computer(session, request, computer_id, latest_only):
    principal = await authenticate(session, request)
    computer = await computer_for_user(session, principal, computer_id)
    rows = list(
        await session.scalars(
            select(BenchmarkResult)
            .join(Computer, Computer.id == BenchmarkResult.computer_id)
            .outerjoin(ComputerOwnership, ComputerOwnership.computer_id == Computer.id)
            .where(
                BenchmarkResult.computer_id == computer.id,
                # A transfer can commit after the initial computer check; read
                # measurements only within the owner scope of this statement.
                or_(
                    owned_filter(ComputerOwnership, principal),
                    and_(
                        ComputerOwnership.id.is_(None),
                        Computer.registered_by_user_id == principal.user_id,
                    ),
                ),
            )
        )
    )
    if not rows:
        # Preserve valid empty inventories while hiding access lost in a transfer.
        await computer_for_user(session, principal, computer_id)
    if rows and latest_only:
        version = max((row.benchmarker_version for row in rows), key=semver_key)
        rows = [row for row in rows if row.benchmarker_version == version]
        # Keep the newest measurement of each name in the latest tooling version.
        # Suites uploaded together share a timestamp, so concurrent complete
        # suites cannot produce a pair assembled from different runs.
        latest = {}
        for row in sorted(rows, key=lambda row: (row.created_at, row.run_id)):
            latest[row.benchmark_name] = row
        rows = list(latest.values())
    return [
        public(row)
        for row in sorted(
            rows,
            key=lambda row: (
                semver_key(row.benchmarker_version),
                row.benchmark_name,
                row.created_at,
                row.run_id,
            ),
        )
    ]


@router.get("/benchmark-results", response_model=list[out.BenchmarkResult])
@router.get("/computers/{computer_id}/benchmark-results", response_model=list[out.BenchmarkResult])
async def list_results(computer_id: UUID, request: Request, latest_only: bool = True):
    """Return the newest result per name in the greatest SemVer, or all runs."""
    async with request.app.state.db.read() as session:
        return await results_for_computer(session, request, computer_id, latest_only)


async def result_for_user(session, request, result_id, *, write=False):
    principal = await authenticate(session, request, "manage" if write else "read")
    row = await session.get(BenchmarkResult, str(result_id))
    if row is None:
        raise HTTPException(404, "Benchmark result not found")
    await computer_for_user(session, principal, row.computer_id, admin=write)
    return row


@router.get("/benchmark-results/{result_id}", response_model=out.BenchmarkResult)
async def get_result(result_id: UUID, request: Request):
    async with request.app.state.db.read() as session:
        return public(await result_for_user(session, request, result_id))


@router.patch("/benchmark-results/{result_id}", response_model=out.BenchmarkResult)
async def update_result(result_id: UUID, data: BenchmarkUpdate, request: Request):
    async with request.app.state.db.write() as session:
        row = await result_for_user(session, request, result_id, write=True)
        row.number = data.number
        await session.flush()
        return public(row)


@router.delete("/benchmark-results/{result_id}", status_code=204)
async def delete_result(result_id: UUID, request: Request):
    async with request.app.state.db.write() as session:
        await session.delete(await result_for_user(session, request, result_id, write=True))


@agent_router.put("/benchmark-results", response_model=list[out.BenchmarkResult])
async def upload_suite(data: BenchmarkSuite, request: Request):
    """All measurements commit together. Works before the first agent session."""
    async with request.app.state.db.write() as session:
        computer = await agent_auth(session, request, require_session=False)
        return await record_run(
            session, computer.id, data.benchmarker_version, data.results, data.run_id
        )
