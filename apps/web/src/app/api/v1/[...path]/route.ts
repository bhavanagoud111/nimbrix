import { connection } from "next/server";
import { proxyToApi } from "@/lib/api/proxy";

async function handle(request: Request, ctx: RouteContext<"/api/v1/[...path]">) {
  await connection(); // always runs at request time; never prerendered or cached
  const { path } = await ctx.params;
  return proxyToApi(request, path);
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };
