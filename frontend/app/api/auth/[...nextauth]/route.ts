export const dynamic = "force-dynamic";

function unavailable() {
  return new Response(null, {
    status: 404,
    headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
  });
}

export const GET = unavailable;
export const POST = unavailable;
