import { describe, expect, it } from "vitest";
import {
  filterOrganizations,
  organizationEntries,
} from "../../../../platform/web/src/lib/organizations";
import type {
  Company,
  Invitation,
  Role,
} from "../../../../platform/web/src/lib/types";

const user = "me";
const company = (
  id: string,
  count: number,
  owner = user,
  role: Role = "creator",
  name = id,
): Company => ({
  id,
  name,
  created_by_user_id: owner,
  my_role: role,
  my_membership_id: `membership-${id}`,
  my_permissions: [],
  computer_count: count,
  member_count: 1,
});
const invite = (
  id: string,
  count: number,
  status: Invitation["status"] = "pending",
  name = id,
): Invitation => ({
  id,
  company_id: `company-${id}`,
  company_name: name,
  role: "user",
  permissions: null,
  effective_permissions: [],
  email: null,
  status,
  expires_at: "2099-01-01T00:00:00Z",
  computer_count: count,
  member_count: 2,
});
const ids = (rows: ReturnType<typeof organizationEntries>) =>
  rows.map((row) => row.id);

describe("organization directory order", () => {
  it("prioritizes invitations, authored organizations, then memberships regardless of computer totals", () => {
    expect(
      ids(
        organizationEntries(
          [
            company("member", 1000, "another", "admin"),
            company("created", 100),
          ],
          [invite("invited", 0)],
          user,
        ),
      ),
    ).toEqual(["invited", "created", "member"]);
  });

  it("sorts numerically descending inside each of the three groups, including zero", () => {
    const counts = [2, 0, 10, 1];
    const companies = counts.flatMap((count) => [
      company(`owned-${count}`, count),
      company(`member-${count}`, count, "another", "user"),
    ]);
    const invitations = counts.map((count) =>
      invite(`invited-${count}`, count),
    );
    expect(ids(organizationEntries(companies, invitations, user))).toEqual([
      "invited-10",
      "invited-2",
      "invited-1",
      "invited-0",
      "owned-10",
      "owned-2",
      "owned-1",
      "owned-0",
      "member-10",
      "member-2",
      "member-1",
      "member-0",
    ]);
  });

  it.each<Role>(["creator", "admin", "user"])(
    "groups a membership with role %s by its actual author",
    (role) => {
      const rows = organizationEntries(
        [
          company("other", 999, "another", role),
          company("mine", 0, user, "user"),
        ],
        [],
        user,
      );
      expect(ids(rows)).toEqual(["mine", "other"]);
      expect(rows.map((row) => row.group)).toEqual([1, 2]);
    },
  );

  it("breaks equal counts by name in every group, then by ID for identical names", () => {
    const rows = organizationEntries(
      [
        company("own-z", 2, user, "creator", "Zulu"),
        company("own-b", 2, user, "creator", "Alpha"),
        company("own-a", 2, user, "creator", "alpha"),
        company("member-z", 2, "other", "admin", "Zulu"),
        company("member-a", 2, "other", "user", "Alpha"),
      ],
      [
        invite("invite-z", 2, "pending", "Zulu"),
        invite("invite-a", 2, "pending", "Alpha"),
      ],
      user,
    );
    expect(ids(rows)).toEqual([
      "invite-a",
      "invite-z",
      "own-a",
      "own-b",
      "own-z",
      "member-a",
      "member-z",
    ]);
  });

  it("uses natural ordering for tied numeric names", () => {
    expect(
      ids(
        organizationEntries(
          [
            company("10", 0, user, "creator", "Team 10"),
            company("2", 0, user, "creator", "Team 2"),
          ],
          [],
          user,
        ),
      ),
    ).toEqual(["2", "10"]);
  });

  it.each<Invitation["status"]>(["accepted", "declined", "revoked", "expired"])(
    "omits %s invitations",
    (status) => {
      expect(
        organizationEntries([], [invite("hidden", 999, status)], user),
      ).toEqual([]);
    },
  );

  it("deduplicates stale invitations for an existing membership and repeated company invitations", () => {
    const a = { ...invite("b", 2), company_id: "same" };
    const b = { ...invite("a", 2), company_id: "same" };
    const stale = { ...invite("stale", 999), company_id: "member" };
    expect(
      ids(
        organizationEntries(
          [company("member", 1, "another", "creator")],
          [a, stale, b],
          user,
        ),
      ),
    ).toEqual(["a", "member"]);
  });

  it("does not mutate either response and is independent of response order", () => {
    const companies = [company("b", 1), company("a", 2)];
    const invitations = [invite("y", 0), invite("x", 3)];
    const before = JSON.stringify({ companies, invitations });
    const expected = ids(organizationEntries(companies, invitations, user));
    expect(
      ids(
        organizationEntries(
          [...companies].reverse(),
          [...invitations].reverse(),
          user,
        ),
      ),
    ).toEqual(expected);
    expect(JSON.stringify({ companies, invitations })).toBe(before);
  });

  it("sorts the entire collection, including records beyond a 200-item API page", () => {
    const companies = Array.from({ length: 401 }, (_, i) =>
      company(String(i), i),
    );
    const rows = organizationEntries(companies, [], user);
    expect(rows).toHaveLength(401);
    expect(rows.map((row) => row.computerCount)).toEqual(
      Array.from({ length: 401 }, (_, i) => 400 - i),
    );
  });

  it("moves an accepted invitation into the membership group, including creator grants", () => {
    const pending = invite("incoming", 7);
    const owned = company("mine", 0);
    expect(ids(organizationEntries([owned], [pending], user))).toEqual([
      "incoming",
      "mine",
    ]);
    const accepted = company(
      pending.company_id,
      7,
      "original-author",
      "creator",
    );
    expect(
      ids(
        organizationEntries(
          [accepted, owned],
          [{ ...pending, status: "accepted" }],
          user,
        ),
      ),
    ).toEqual(["mine", pending.company_id]);
  });

  it("inserts a new zero-computer organization after existing owned ones and before all memberships", () => {
    expect(
      ids(
        organizationEntries(
          [
            company("member", 999, "other", "user"),
            company("old", 2),
            company("new", 0),
          ],
          [invite("pending", 0)],
          user,
        ),
      ),
    ).toEqual(["pending", "old", "new", "member"]);
  });

  it("handles empty collections", () =>
    expect(organizationEntries([], [], user)).toEqual([]));
});

describe("temporary organization name search", () => {
  const rows = organizationEntries(
    [
      company("owned", 4, user, "creator", "Квантовая Лаборатория"),
      company("member", 1, "other", "admin", "Research Lab"),
    ],
    [invite("pending", 2, "pending", "LAB Collective")],
    user,
  );
  it.each(["lab", " LAB ", "Lab"])(
    "matches names without case or outer whitespace: %s",
    (query) => {
      expect(ids(filterOrganizations(rows, query))).toEqual([
        "pending",
        "member",
      ]);
    },
  );
  it("matches Cyrillic and substrings", () =>
    expect(ids(filterOrganizations(rows, "ЛАБОРАТ"))).toEqual(["owned"]));
  it("keeps priority and count order in filtered results", () => {
    const matching = organizationEntries(
      [
        company("own-1", 1, user, "creator", "Lab 1"),
        company("own-9", 9, user, "creator", "Lab 9"),
        company("member-99", 99, "other", "user", "Lab 99"),
      ],
      [invite("invite-0", 0, "pending", "Lab 0")],
      user,
    );
    expect(ids(filterOrganizations(matching, "lab"))).toEqual([
      "invite-0",
      "own-9",
      "own-1",
      "member-99",
    ]);
  });
  it("returns the full list for a blank query and no results for unknown names", () => {
    expect(filterOrganizations(rows, "  ")).toEqual(rows);
    expect(filterOrganizations(rows, "unknown")).toEqual([]);
  });
  it("searches names only and treats regular-expression symbols as literal text", () => {
    expect(filterOrganizations(rows, "owned")).toEqual([]);
    expect(filterOrganizations(rows, ".*")).toEqual([]);
  });
});
