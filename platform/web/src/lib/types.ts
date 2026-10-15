export interface User {
  id: string;
  email: string;
  first_name: string;
  last_name: string;
  nickname: string | null;
  avatar_version: string | null;
}
export interface Company {
  id: string;
  name: string;
  created_by_user_id: string;
  my_role: Role;
  my_permissions: Permission[];
  my_membership_id: string;
}
export type Role = "creator" | "admin" | "user";
export type Permission =
  | "members.read"
  | "members.manage"
  | "resources.read"
  | "resources.manage"
  | "execution.read"
  | "execution.run"
  | "tokens.read"
  | "tokens.manage"
  | "company.manage";
export type MemberProfile = Omit<User, "email">;
export interface Membership {
  id: string;
  user_id: string;
  company_id: string;
  role: Role;
  permissions: Permission[] | null;
  effective_permissions: Permission[];
  user: MemberProfile;
}
export interface Invitation {
  id: string;
  company_id: string;
  company_name: string;
  role: Role;
  permissions: Permission[] | null;
  effective_permissions: Permission[];
  email: string | null;
  status: "pending" | "accepted" | "declined" | "revoked" | "expired";
  expires_at: string;
}
export interface CreatedInvitation extends Invitation {
  token: string;
}
export interface Resource {
  code: string;
  total: number;
  free: number;
  occupied: number;
}
export interface Summary {
  as_of: string;
  only_available: boolean;
  resources: Resource[];
  computer_count: number;
}
export interface CapacityPoint {
  at: string;
  cpu_millis: number;
  memory_bytes: number;
  cpu_occupied_millis: number;
  memory_occupied_bytes: number;
}
export interface CapacityHistory {
  from_time: string;
  to_time: string;
  as_of: string;
  available_from: string;
  points: CapacityPoint[];
}
export interface Computer {
  id: string;
  name: string | null;
  os: string;
  architecture: string;
  runtime: string;
  cpu_millis: number;
  memory_bytes: number;
  reserved_cpu: number;
  reserved_memory: number;
  enabled: boolean;
  online: boolean;
  session_id: string | null;
  lease_expires_at: string | null;
}
export interface BenchmarkResult {
  id: string;
  computer_id: string;
  benchmark_name: string;
  benchmarker_version: string;
  number: number;
}
export interface Pool {
  id: string;
  kind: "users" | "companies";
  name: string;
}
