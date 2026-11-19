import type { Computer } from "./types";

export function computerAvailability(computer: Computer): {
  status: "disabled" | "online" | "offline";
  available: boolean;
} {
  // Prefer the API clock. Older API versions only expose the stored lease.
  const online = computer.session_active ??
    Boolean(
      computer.online &&
        computer.session_id &&
        computer.lease_expires_at &&
        Date.parse(computer.lease_expires_at) > Date.now(),
    );
  const status = !computer.enabled ? "disabled" : online ? "online" : "offline";
  return { status, available: status === "online" && computer.accepting_work };
}
