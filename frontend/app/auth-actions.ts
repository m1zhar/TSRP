"use server";

import { redirect } from "next/navigation";
import { AUTH_READY, signIn, signOut } from "@/auth";
import { safeReturnPath } from "@/lib/safe-return-path";

export async function signInAction(formData: FormData) {
  if (!AUTH_READY) redirect("/signin?setup=required");
  const callbackUrl = safeReturnPath(formData.get("callbackUrl"));
  await signIn("tsrp-oidc", { redirectTo: callbackUrl });
}

export async function signOutAction() {
  await signOut({ redirectTo: "/" });
}
