import type { Company } from "./types";

export type OrganizationImage = "avatar" | "background";

export function organizationImagePath(company: Company, kind: OrganizationImage) {
  const version = company[`${kind}_version`];
  return version ? `companies/${company.id}/images/${kind}?v=${encodeURIComponent(version)}` : null;
}
