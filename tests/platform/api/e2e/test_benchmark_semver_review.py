"""SemVer identifier edge cases and strict benchmark validation over HTTP/SQL."""

import json
import sys

import pytest

pytestmark = pytest.mark.anyio


async def create_result(api, computer, version, *, name="future_metric", number=1):
    """Create a benchmark result through the API and return its persisted representation."""
    response = await api.post(
        "/v1/benchmark-results",
        json={
            "computer_id": computer["id"],
            "benchmark_name": name,
            "benchmarker_version": version,
            "number": number,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_history_orders_prerelease_identifiers_and_large_core_components(api, computer):
    """History follows SemVer identifier ordering, including numeric prereleases and large core components, and latest selects the greatest version."""
    # Numeric identifiers precede every nonnumeric identifier; a shared prefix
    # sorts before a longer prerelease, and ASCII comparison is case sensitive.
    versions = [
        "1.0.0-0",
        "1.0.0-2",
        "1.0.0-10",
        "1.0.0-999999999999999999999999999999",
        "1.0.0--",
        "1.0.0-A",
        "1.0.0-Z",
        "1.0.0-a",
        "1.0.0-alpha",
        "1.0.0-alpha.0",
        "1.0.0-alpha.0.0",
        "1.0.0-alpha.2",
        "1.0.0-alpha.10",
        "1.0.0-alpha.A",
        "1.0.0-alpha.a",
        "1.0.0",
        "999999999999999999999999999999.0.0",
        "1000000000000000000000000000000.0.0",
    ]
    created = {}
    for version in reversed(versions):
        created[version] = await create_result(api, computer, version)

    query = {"computer_id": computer["id"], "latest_only": False}
    response = await api.get("/v1/benchmark-results", params=query)
    assert response.status_code == 200, response.text
    assert response.json() == [created[version] for version in versions]

    path = f"/v1/computers/{computer['id']}/benchmark-results"
    assert (await api.get(path, params={"latest_only": False})).json() == response.json()
    assert (await api.get(path)).json() == [created[versions[-1]]]


@pytest.mark.parametrize("base", ["2.0.0", "2.0.0-rc.1"])
async def test_build_metadata_ties_use_lexical_order_and_preserve_distinct_rows(
    api, computer, base
):
    """Build metadata ties use lexical full-version order while distinct versions retain separate rows and repeated versions retain their UUID."""
    # Metadata has equal SemVer precedence, so the API's lexical tie rule makes
    # build.2 newer than build.10 and a metadata version newer than the bare one.
    versions = [base, base + "+build.10", base + "+build.2"]
    winner = await create_result(api, computer, versions[-1], name="new_metric")
    older = [
        await create_result(api, computer, version, name="old_metric") for version in versions[:2]
    ]
    replacement = await create_result(api, computer, versions[-1], name="new_metric", number=17)
    assert replacement["id"] == winner["id"]

    path = f"/v1/computers/{computer['id']}/benchmark-results"
    assert (await api.get(path)).json() == [replacement]
    assert (await api.get(path, params={"latest_only": False})).json() == [*older, replacement]
    assert len({row["id"] for row in [*older, replacement]}) == 3


@pytest.mark.parametrize(
    "version",
    [
        "0.0.0-01a",
        "0.0.0-00-",
        "0.0.0+00.01",
        "0.0.0-a-b.0+001.build-2",
        "1.0.0+" + "a" * 194,
    ],
)
async def test_valid_unusual_semver_is_stored_without_normalization(api, computer, version):
    """Valid uncommon SemVer forms survive storage and retrieval exactly as submitted."""
    row = await create_result(api, computer, version)
    assert row["benchmarker_version"] == version
    response = await api.get(f"/v1/benchmark-results/{row['id']}")
    assert response.status_code == 200, response.text
    assert response.json() == row


@pytest.mark.parametrize(
    "version",
    [
        "1.2.3-alpha.01",
        "1.2.3-1.00.a",
        "1.2.3+build..1",
        "1.2.3+build.",
        "1.2.3+build+extra",
        "1.2.3-alpha_1",
        "1.2.3 ",
        " 1.2.3",
        "1.2.3\n",
        "1.2.3-١",
        123,
        True,
        None,
    ],
)
async def test_malformed_semver_and_nonstring_versions_do_not_replace_existing_row(
    api, computer, version
):
    """Invalid version syntax or types cannot overwrite a valid result or add a new history entry."""
    original = await create_result(api, computer, "1.2.3", number=29)
    response = await api.post(
        "/v1/benchmark-results",
        json={
            "computer_id": computer["id"],
            "benchmark_name": original["benchmark_name"],
            "benchmarker_version": version,
            "number": 99,
        },
    )
    assert response.status_code == 422, response.text
    response = await api.get(
        f"/v1/computers/{computer['id']}/benchmark-results", params={"latest_only": False}
    )
    assert response.status_code == 200, response.text
    assert response.json() == [original]


async def test_score_float_boundaries_round_trip_in_create_and_patch(api, computer):
    """Creating and updating scores preserves supported finite floating-point boundary values and valid integer inputs."""
    row = await create_result(api, computer, "1.0.0", number=sys.float_info.max)
    assert row["number"] == sys.float_info.max
    path = f"/v1/benchmark-results/{row['id']}"
    for number in [5e-324, 0, 42]:
        response = await api.patch(path, json={"number": number})
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated == {**row, "number": number}
        assert (await api.get(path)).json() == updated


async def test_patch_rejects_nonfinite_wire_values_and_nonnumeric_types_without_mutation(
    api, computer
):
    """Invalid numeric wire values and wrong JSON types return validation errors while preserving the original score."""
    row = await create_result(api, computer, "1.0.0", number=32)
    path = f"/v1/benchmark-results/{row['id']}"
    wire_numbers = ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"]
    wire_numbers += [json.dumps(value) for value in [False, [], {}, 10**400]]
    for number in wire_numbers:
        response = await api.patch(
            path,
            content='{"number":' + number + "}",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422, (number, response.text)
        assert (await api.get(path)).json() == row


async def test_future_name_length_boundary_and_strict_identifier_types(api, computer):
    """The longest supported future benchmark name is accepted, while malformed or nonstring names cannot create a newer suite."""
    name = "future_" + "x" * 193
    row = await create_result(api, computer, "1.0.0", name=name)
    assert row["benchmark_name"] == name
    for invalid_name in ["Future_metric", "1_metric", "future.metric", "future_metric\n", 1, True]:
        response = await api.post(
            "/v1/benchmark-results",
            json={
                "computer_id": computer["id"],
                "benchmark_name": invalid_name,
                "benchmarker_version": "2.0.0",
                "number": 1,
            },
        )
        assert response.status_code == 422, (invalid_name, response.text)
    assert (await api.get(f"/v1/computers/{computer['id']}/benchmark-results")).json() == [row]
