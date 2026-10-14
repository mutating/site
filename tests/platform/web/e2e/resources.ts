import { randomUUID } from "node:crypto";
import type { APIRequestContext } from "@playwright/test";
import { API, expect, headers, type Account } from "./fixtures";

export const GiB = 1024 ** 3;
export const historyNow = new Date("2026-01-08T00:00:00Z");

// Exercise real scheduling/reservations, without an executing agent or fabricated responses.
export async function reserve(
  request: APIRequestContext,
  account: Account,
  cpu: number,
  memory: number,
  companyId?: string,
) {
  const owner: Record<string, string> = companyId
    ? { company_id: companyId }
    : {};
  const artifact = await request.post(`${API}/artifacts`, {
    headers: {
      ...headers(account),
      "Content-Type": "application/octet-stream",
    },
    params: { filename: "state.tar", ...owner },
    data: Buffer.from("initial state"),
  });
  expect(artifact.status()).toBe(201);
  const response = await request.post(`${API}/isolates`, {
    headers: { ...headers(account), "Idempotency-Key": randomUUID() },
    data: {
      ...owner,
      state_artifact_id: (await artifact.json()).id,
      image: "python:3.12",
      os: "linux",
      architecture: "amd64",
      runtime: "oci",
      cpu_millis: cpu,
      memory_bytes: memory,
      disk_bytes: 4096,
    },
  });
  expect(response.status()).toBe(202);
  const isolate = await response.json();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(`${API}/isolates/${isolate.id}`, {
              headers: headers(account),
            })
          ).json()
        ).reserved,
    )
    .toBe(true);
}

export async function seedHistory(
  request: APIRequestContext,
  computerId: string,
) {
  const response = await request.post(
    "http://127.0.0.1:18100/__test__/history",
    {
      data: { computer_id: computerId },
    },
  );
  expect(response.status()).toBe(200);
}
