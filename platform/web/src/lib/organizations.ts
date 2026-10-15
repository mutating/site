import type { Company, Invitation } from "./types";

export type OrganizationEntry =
  | {
      kind: "company";
      group: 1 | 2;
      name: string;
      company: Company;
      computerCount: number;
      id: string;
    }
  | {
      kind: "invitation";
      group: 0;
      name: string;
      invitation: Invitation;
      computerCount: number;
      id: string;
    };

// Match the database's binary Unicode order, including characters outside BMP.
const compare = (a: string, b: string) => {
  const left = Array.from(a),
    right = Array.from(b);
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    const difference = left[i].codePointAt(0)! - right[i].codePointAt(0)!;
    if (difference) return difference;
  }
  return left.length - right.length;
};
export function organizationNameKey(name: string) {
  return name
    .normalize("NFC")
    .toLowerCase()
    .replace(/[0-9]+/g, (value) => {
      const digits = value.replace(/^0+/, "") || "0";
      return `\x01${String(digits.length).padStart(3, "0")}${digits}`;
    });
}
const nameKey = (entry: OrganizationEntry) => {
  const row = entry.kind === "company" ? entry.company : entry.invitation;
  return row.name_key || organizationNameKey(entry.name);
};

export function organizationEntries(
  companies: readonly Company[],
  invitations: readonly Invitation[],
  userId: string,
): OrganizationEntry[] {
  const companyIds = new Set(companies.map((company) => company.id));
  const invited = new Set<string>();
  const entries: OrganizationEntry[] = companies.map((company) => ({
    kind: "company",
    group: company.created_by_user_id === userId ? 1 : 2,
    name: company.name,
    id: company.id,
    computerCount: company.computer_count,
    company,
  }));
  for (const invitation of [...invitations].sort((a, b) =>
    a.id.localeCompare(b.id),
  )) {
    if (
      invitation.status !== "pending" ||
      companyIds.has(invitation.company_id) ||
      invited.has(invitation.company_id)
    )
      continue;
    invited.add(invitation.company_id);
    entries.push({
      kind: "invitation",
      group: 0,
      name: invitation.company_name,
      id: invitation.id,
      computerCount: invitation.computer_count,
      invitation,
    });
  }
  return entries.sort(
    (a, b) =>
      a.group - b.group ||
      b.computerCount - a.computerCount ||
      compare(nameKey(a), nameKey(b)) ||
      compare(
        a.kind === "company" ? a.id : a.invitation.company_id,
        b.kind === "company" ? b.id : b.invitation.company_id,
      ),
  );
}

export const ORGANIZATIONS_PAGE_SIZE = 20;
export function organizationPage(params: URLSearchParams) {
  const value = Number(params.get("page") || "1");
  return Number.isSafeInteger(value) && value > 0
    ? Math.min(value, 1_000_000)
    : 1;
}
export function organizationSearchParams(
  params: URLSearchParams,
  query: string,
) {
  const next = new URLSearchParams(params);
  if (query) next.set("q", query);
  else next.delete("q");
  next.delete("page");
  return next;
}
