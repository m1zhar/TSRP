import { redirect } from "next/navigation";

type SearchParams = Record<string, string | string[] | undefined>;

function safeCallback(value: string | string[] | undefined): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/";
  try {
    const parsed = new URL(value, "https://tsrp.invalid");
    return parsed.origin === "https://tsrp.invalid" ? `${parsed.pathname}${parsed.search}${parsed.hash}` : "/";
  } catch {
    return "/";
  }
}

export default async function SignInPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const callbackUrl = safeCallback(params.callbackUrl);
  redirect(callbackUrl);
}
