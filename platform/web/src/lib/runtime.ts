// Technical runtime identifiers stay stable for enrolled agents and stored
// computers; only their user-facing label follows the product name.
export const COMPUTE_RUNTIME = "powercell-ubuntu-arm64-v1";

export function runtimeLabel(runtime: string): string {
  return runtime === COMPUTE_RUNTIME ? "Mutating Compute · Linux ARM64" : runtime;
}
