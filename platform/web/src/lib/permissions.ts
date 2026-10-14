import type { MemberProfile, Permission, Role } from "./types";
import type { MessageKey } from "./i18n";

export const permissionLabels: Record<Permission, MessageKey> = {
  "members.read": "permissionMembersRead",
  "members.manage": "permissionMembersManage",
  "resources.read": "permissionResourcesRead",
  "resources.manage": "permissionResourcesManage",
  "execution.read": "permissionExecutionRead",
  "execution.run": "permissionExecutionRun",
  "tokens.read": "permissionTokensRead",
  "tokens.manage": "permissionTokensManage",
  "company.manage": "permissionCompanyManage",
};
export const permissions = Object.keys(permissionLabels) as Permission[];
export const rolePermissions = (role: Role): Permission[] =>
  role === "user"
    ? [
        "members.read",
        "resources.read",
        "execution.read",
        "execution.run",
        "tokens.read",
      ]
    : [...permissions];
export const memberName = (user: MemberProfile) =>
  [user.first_name, user.last_name].filter(Boolean).join(" ") ||
  user.nickname ||
  user.id.slice(0, 8);
