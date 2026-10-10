// Liveness of the web server itself (used by the container healthcheck).
export function GET() {
  return Response.json({ status: "ok", service: "nimbrix-web" });
}
