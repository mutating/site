import {
  test as base,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { randomUUID } from "node:crypto";
export const API = "http://127.0.0.1:18100/api/v1";
export const password = "  a correct horse battery staple  ";
export type Account = {
  id: string;
  email: string;
  nickname: string;
  token: string;
  first_name: string;
};
export async function makeAccount(
  request: APIRequestContext,
  firstName = "Алиса",
): Promise<Account> {
  const nickname = "person_" + randomUUID().replaceAll("-", "");
  const email = nickname + "@example.com";
  const created = await request.post(`${API}/users`, {
    data: { email, password, first_name: firstName, nickname },
  });
  expect(created.status()).toBe(201);
  const login = await request.post(`${API}/auth/login`, {
    data: { login: email, password },
  });
  expect(login.status()).toBe(200);
  return {
    ...(await created.json()),
    token: (await login.json()).access_token,
  };
}
export const headers = (account: Account) => ({
  Authorization: `Bearer ${account.token}`,
});
export async function login(
  page: Page,
  account: Account,
  identifier = account.email,
) {
  await page.goto("");
  await page.getByLabel("Email или ник").fill(identifier);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: `Привет, ${account.first_name}` }),
  ).toBeVisible();
  await expect(page).toHaveURL("http://127.0.0.1:18101/");
}
export async function addComputer(
  request: APIRequestContext,
  account: Account,
  name: string,
  companyId?: string,
  online = true,
  resources: {
    cpu_millis?: number;
    memory_bytes?: number;
    enabled?: boolean;
  } = {},
) {
  const created = await request.post(`${API}/computers`, {
    headers: headers(account),
    data: {
      name,
      os: "linux",
      architecture: "amd64",
      runtime: "oci",
      cpu_millis: 8000,
      memory_bytes: 8 * 1024 ** 3,
      disk_bytes: 64 * 1024 ** 3,
      ...(companyId ? { company_id: companyId } : {}),
      ...resources,
    },
  });
  expect(created.status()).toBe(201);
  const computer = await created.json();
  if (online) {
    const credential = await request.post(
      `${API}/computers/${computer.id}/credentials`,
      { headers: headers(account) },
    );
    const response = await request.post(
      "http://127.0.0.1:18100/agent/v1/session",
      {
        headers: {
          Authorization: `Bearer ${(await credential.json()).token}`,
          "Idempotency-Key": randomUUID(),
        },
        data: { clean_start: true },
      },
    );
    expect(response.status()).toBe(200);
  }
  return computer;
}
export const test = base.extend<{ account: Account }>({
  account: async ({ request }, use) => {
    await use(await makeAccount(request));
  },
});
export { expect };
