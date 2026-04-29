import { proxyToBackend } from "../_proxy";

export async function GET() {
  return proxyToBackend("/api/sources");
}
