import { proxyToBackend } from "../_proxy";

export async function POST(request: Request) {
  const body = await request.text();
  return proxyToBackend("/api/ask", {
    method: "POST",
    body,
  });
}
