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
    for name, expected in fields.items():
        model = models[name]
        assert model["type"] == "object"
        assert model["additionalProperties"] is False
        assert set(model["properties"]) == expected
        assert set(model["required"]) == expected
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
        "computer_id",
        "benchmark_name",
        "number",
        "benchmarker_version",
    }
    assert output["properties"]["number"]["type"] == "number"
    assert "enum" not in output["properties"]["benchmark_name"]


async def test_openapi_benchmark_methods_statuses_and_list_parameters(client):
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


async def test_invalid_nested_json_cannot_replace_an_already_uploaded_result(
    api, agent_api, computer
):
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
