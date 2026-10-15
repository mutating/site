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

const names = new Intl.Collator("ru", { sensitivity: "base", numeric: true });

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
      names.compare(a.name, b.name) ||
      a.id.localeCompare(b.id),
  );
}

// Temporary browser-side name matching. Server-side search can replace this independently.
export function filterOrganizations(
  entries: readonly OrganizationEntry[],
  query: string,
) {
  const normalized = query.trim().normalize("NFC").toLocaleLowerCase("ru");
  return entries.filter((entry) =>
    entry.name.normalize("NFC").toLocaleLowerCase("ru").includes(normalized),
  );
}
