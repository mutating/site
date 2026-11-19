"""Client-facing benchmark schemas and JSON round trips over real HTTP."""

import json
import sys
from datetime import datetime
from uuid import UUID

import pytest

pytestmark = pytest.mark.anyio

OWNER_RESULTS = "/v1/benchmark-results"
AGENT_RESULTS = "/agent/v1/benchmark-results"
RESULT_REF = {"$ref": "#/components/schemas/BenchmarkResult"}


async def test_openapi_benchmark_input_and_output_contracts(client):
    """OpenAPI describes the required result fields and bounded suite inputs while allowing future benchmark names."""
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    models = schema["components"]["schemas"]
    fields = {
        "BenchmarkCreate": {"computer_id", "benchmark_name", "number", "benchmarker_version"},
        "BenchmarkUpdate": {"number"},
        "BenchmarkMeasurement": {"benchmark_name", "number"},
        "BenchmarkSuite": {"benchmarker_version", "results"},
    }
    measurement_metadata = {"pi_equivalent", "reference_machine", "reference_run"}
    run_metadata = {"aggregated_scores"}
    optional_fields = {
        "BenchmarkCreate": measurement_metadata | run_metadata | {"run_id"},
        "BenchmarkMeasurement": measurement_metadata,
        "BenchmarkSuite": run_metadata | {"run_id"},
        "BenchmarkUpdate": set(),
    }
    for name, expected in fields.items():
        model = models[name]
        assert model["type"] == "object"
        assert model["additionalProperties"] is False
        optional = optional_fields[name]
        assert set(model["properties"]) == expected | optional
        assert set(model["required"]) == expected
        for field in optional:
            assert {"type": "null"} in model["properties"][field]["anyOf"]
    for name in ["BenchmarkCreate", "BenchmarkMeasurement"]:
        properties = models[name]["properties"]
        benchmark_name = properties["benchmark_name"]
        assert benchmark_name["type"] == "string"
        assert "enum" not in benchmark_name
        assert benchmark_name["minLength"] == 1
        assert benchmark_name["maxLength"] == 200
        assert properties["number"]["type"] == "number"
        assert properties["number"]["minimum"] == 0
    results = models["BenchmarkSuite"]["properties"]["results"]
    assert results["type"] == "array"
    assert results["minItems"] == 1
    assert results["maxItems"] == 1024
    assert results["items"] == {"$ref": "#/components/schemas/BenchmarkMeasurement"}
    output = models["BenchmarkResult"]
    assert set(output["required"]) == {
        "id",
        "created_at",
        "run_id",
        "computer_id",
        "benchmark_name",
        "number",
        "benchmarker_version",
    }
    assert (
        set(output["properties"]) == set(output["required"]) | measurement_metadata | run_metadata
    )
    assert output["properties"]["number"]["type"] == "number"
    assert "enum" not in output["properties"]["benchmark_name"]


async def test_openapi_benchmark_methods_statuses_and_list_parameters(client):
    """Published routes expose the expected payload models, success statuses, and required computer filtering for both listing forms."""
    paths = (await client.get("/openapi.json")).json()["paths"]
    for path, method, model, success in [
        (OWNER_RESULTS, "post", "BenchmarkCreate", "201"),
        (OWNER_RESULTS + "/{result_id}", "patch", "BenchmarkUpdate", "200"),
        (AGENT_RESULTS, "put", "BenchmarkSuite", "200"),
    ]:
        operation = paths[path][method]
        body = operation["requestBody"]
        assert body["required"] is True
        assert set(body["content"]) == {"application/json"}
        assert body["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{model}"
        }
        responses = operation["responses"]
        assert success in responses and "422" in responses
        result = responses[success]["content"]["application/json"]["schema"]
        if path == AGENT_RESULTS:
            assert result["type"] == "array"
            assert result["items"] == RESULT_REF
        else:
            assert result == RESULT_REF
    for path, identifier_location in [
        (OWNER_RESULTS, "query"),
        ("/v1/computers/{computer_id}/benchmark-results", "path"),
    ]:
        operation = paths[path]["get"]
        parameters = {item["name"]: item for item in operation["parameters"]}
        assert parameters["computer_id"]["in"] == identifier_location
        assert parameters["computer_id"]["required"] is True
        assert parameters["computer_id"]["schema"]["format"] == "uuid"
        assert parameters["latest_only"]["in"] == "query"
        assert parameters["latest_only"]["required"] is False
        assert parameters["latest_only"]["schema"]["type"] == "boolean"
        assert parameters["latest_only"]["schema"]["default"] is True
        result = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert result["type"] == "array"
        assert result["items"] == RESULT_REF
    deletion = paths[OWNER_RESULTS + "/{result_id}"]["delete"]
    assert "content" not in deletion["responses"]["204"]


async def test_worker_future_names_and_partial_newer_version_match_both_list_routes(
    api, client, computer
):
    """Future benchmark names upload individually, and both listing routes return only the newest version even when that version is incomplete."""
    credential = await api.post(f"/v1/computers/{computer['id']}/credentials")
    assert credential.status_code == 201, credential.text
    headers = {"Authorization": "Bearer " + credential.json()["token"]}

    async def upload(version, name, number):
        response = await client.put(
            AGENT_RESULTS,
            headers=headers,
            json={
                "benchmarker_version": version,
                "results": [{"benchmark_name": name, "number": number}],
            },
        )
        assert response.status_code == 200, response.text
        return response.json()[0]

    old_cpu = await upload("0.0.2", "single_core", 5)
    old_gpu = await upload("0.0.2", "future_gpu_v42", 8)
    latest_gpu = await upload("0.0.3", "future_gpu_v42", 10)
    # An older measurement arriving later must not fill gaps in the newer suite.
    old_disk = await upload("0.0.2", "future_disk_v9", 12)
    nested = f"/v1/computers/{computer['id']}/benchmark-results"
    for path, query in [(nested, {}), (OWNER_RESULTS, {"computer_id": computer["id"]})]:
        response = await api.get(path, params=query)
        assert response.status_code == 200, response.text
        assert response.json() == [latest_gpu]
        history = await api.get(path, params={**query, "latest_only": "false"})
        assert history.status_code == 200, history.text
        assert {row["id"] for row in history.json()} == {
            old_cpu["id"],
            old_gpu["id"],
            latest_gpu["id"],
            old_disk["id"],
        }
    latest_cpu = await upload("0.0.3", "single_core", 20)
    response = await api.get(nested)
    assert {row["id"] for row in response.json()} == {latest_cpu["id"], latest_gpu["id"]}


async def test_worker_finite_double_extremes_round_trip_without_score_truncation(
    api, agent_api, computer
):
    """Worker uploads preserve finite double precision, including subnormal and maximum values, through storage and owner reads."""
    numbers = [0.0, 5e-324, sys.float_info.min, 0.9999999999999999, 2.0**53, sys.float_info.max]
    payload = {
        "benchmarker_version": "0.0.2",
        "results": [
            {"benchmark_name": f"future_double_{index}", "number": number}
            for index, number in enumerate(numbers)
        ],
    }
    response = await agent_api.put(AGENT_RESULTS, json=payload)
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == len(numbers)
    for row, measurement in zip(rows, payload["results"], strict=True):
        assert set(row) == {
            "id",
            "created_at",
            "run_id",
            "computer_id",
            "benchmark_name",
            "number",
            "benchmarker_version",
        }
        assert str(UUID(row["id"])) == row["id"]
        assert datetime.fromisoformat(row["created_at"]).tzinfo is not None
        assert row["computer_id"] == computer["id"]
        assert row["benchmarker_version"] == payload["benchmarker_version"]
        assert row["benchmark_name"] == measurement["benchmark_name"]
        assert row["number"].hex() == measurement["number"].hex()
        detail = await api.get(f"{OWNER_RESULTS}/{row['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json() == row


async def test_agent_accepts_benchset_pi_scores_and_reference_calibration(api, agent_api, computer):
    """Measurement and run fields survive upload and owner reads in the existing result shape."""
    calibration = {
        "reference_machine": {
            "name": "Raspberry Pi 5 Model B Rev 1.0",
            "short_name": "pi",
            "os_name": "Ubuntu Server",
            "os_version": "26.04 LTS",
            "cpu_architecture": "aarch64",
            "memory_bytes": 8589934592,
            "cpu_cores": 4,
        },
        "reference_run": {
            "started_at_unix_seconds": 1792767590.517391,
            "start_temperature_c": 41.35,
            "end_temperature_c": 66.65,
            "warmup_seconds": 1248.0,
            "results": [
                {"benchmark": "single_core", "score": 47231.227029007096},
                {"benchmark": "multi_core", "score": 181953.17419139884},
            ],
        },
    }
    measurements = [
        {
            "benchmark_name": "single_core",
            "number": 23615.613514503548,
            "pi_equivalent": 0.5,
            **calibration,
        },
        {"benchmark_name": "multi_core", "number": 363906.3483827977, "pi_equivalent": 2.0},
    ]
    scores = {"pi_equivalent": 1.0}
    response = await agent_api.put(
        AGENT_RESULTS,
        json={"benchmarker_version": "0.0.9", "aggregated_scores": scores, "results": measurements},
    )
    assert response.status_code == 200, response.text
    single, multi = rows = response.json()
    assert "reference_machine" not in multi and "reference_run" not in multi
    for row, measurement in zip(rows, measurements, strict=True):
        assert row["aggregated_scores"] == scores
        assert {key: row[key] for key in measurement} == measurement
        detail = await api.get(f"{OWNER_RESULTS}/{row['id']}")
        assert detail.json() == row
    assert single["run_id"] == multi["run_id"]
    for path in [
        f"{OWNER_RESULTS}?computer_id={computer['id']}",
        f"/v1/computers/{computer['id']}/benchmark-results",
    ]:
        assert (await api.get(path)).json() == [multi, single]


async def test_calibration_text_fields_have_a_bounded_length(api, agent_api, computer):
    """Oversized calibration labels are rejected before storage, while 200 characters round-trip."""
    labels = ("name", "short_name", "os_name", "os_version", "cpu_architecture")
    machine = {**dict.fromkeys(labels, "x" * 200), "memory_bytes": 8589934592, "cpu_cores": 4}
    measurement = {"benchmark_name": "single_core", "number": 23615.613514503548}
    for field in labels:
        response = await agent_api.put(
            AGENT_RESULTS,
            json={
                "benchmarker_version": "0.0.9",
                "results": [{**measurement, "reference_machine": {**machine, field: "x" * 201}}],
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"][0]["loc"][-2:] == ["reference_machine", field]
    path = f"/v1/computers/{computer['id']}/benchmark-results"
    assert (await api.get(path)).json() == []

    response = await agent_api.put(
        AGENT_RESULTS,
        json={
            "benchmarker_version": "0.0.9",
            "results": [{**measurement, "reference_machine": machine}],
        },
    )
    assert response.status_code == 200, response.text
    assert (await api.get(path)).json()[0]["reference_machine"] == machine


async def test_invalid_nested_json_cannot_replace_an_already_uploaded_result(
    api, agent_api, computer
):
    """Malformed suite structures and nested measurements return JSON validation errors without overwriting a previously uploaded result."""
    valid = {"benchmark_name": "single_core", "number": 123.5}
    good = {"benchmarker_version": "0.0.2", "results": [valid]}
    response = await agent_api.put(AGENT_RESULTS, json=good)
    assert response.status_code == 200, response.text
    original = response.json()
    malformed = [
        None,
        [],
        {},
        {"results": [valid]},
        {"benchmarker_version": "0.0.2"},
        {"benchmarker_version": "0.0.2", "results": None},
        {"benchmarker_version": "0.0.2", "results": valid},
        {"benchmarker_version": 2, "results": [valid]},
    ]
    for bad in [
        None,
        [],
        1,
        {"benchmark_name": "future_core"},
        {"number": 1},
        {"benchmark_name": "future_core", "number": True},
        {"benchmark_name": "future_core", "number": "1.5"},
        {"benchmark_name": "future_core", "number": 1, "computer_id": computer["id"]},
        {"benchmark_name": "future_core", "number": 1, "benchmarker_version": "0.0.3"},
    ]:
        malformed.append(
            {
                "benchmarker_version": "0.0.2",
                "results": [{**valid, "number": 999}, bad],
            }
        )
    for payload in malformed:
        # Explicit bytes also send JSON null, which HTTPX's json=None omits.
        response = await agent_api.put(
            AGENT_RESULTS,
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422, (payload, response.text)
        assert response.headers["content-type"].startswith("application/json")
        assert isinstance(response.json()["detail"], list)
        stored = await api.get(f"/v1/computers/{computer['id']}/benchmark-results")
        assert stored.json() == original


async def test_agent_nonfinite_wire_numbers_return_json_validation_errors(api, agent_api, computer):
    """Nonfinite or overflowing numeric wire values produce JSON validation responses and leave the computer without measurements."""
    for value in ["NaN", "Infinity", "-Infinity", "1e999", "1" + "0" * 400]:
        body = (
            '{"benchmarker_version":"0.0.2","results":'
            '[{"benchmark_name":"single_core","number":' + value + "}]}"
        )
        response = await agent_api.put(
            AGENT_RESULTS, content=body, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422, (value, response.text)
        assert response.headers["content-type"].startswith("application/json")
        assert isinstance(response.json()["detail"], list)
    assert (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json() == []


async def test_invalid_pi_equivalents_reject_the_entire_upload(api, agent_api, computer):
    """Both Pi equivalents must be finite nonnegative numbers when supplied."""
    for value in [-1, "1.0", True, float("nan"), float("inf")]:
        for field in ["measurement", "aggregate"]:
            payload = {
                "benchmarker_version": "0.0.9",
                "results": [
                    {"benchmark_name": "single_core", "number": 100},
                    {"benchmark_name": "multi_core", "number": 200},
                ],
            }
            if field == "measurement":
                payload["results"][1]["pi_equivalent"] = value
            else:
                payload["aggregated_scores"] = {"pi_equivalent": value}
            response = await agent_api.put(
                AGENT_RESULTS,
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 422, response.text
            assert response.json()["detail"][0]["loc"][-1] == "pi_equivalent"
    assert (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json() == []
