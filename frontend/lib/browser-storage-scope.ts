import { createHmac } from "node:crypto";

const SUBJECT_LIMIT = 512;

/** Create a stable, issuer-scoped browser-storage namespace without retaining the OIDC subject. */
export function opaqueBrowserStorageScope(secret: unknown, issuer: unknown, subject: unknown): string | null {
  if (typeof secret !== "string" || secret.length < 32
    || typeof issuer !== "string" || issuer.length < 8 || issuer.length > SUBJECT_LIMIT
    || typeof subject !== "string" || subject.length < 1 || subject.length > SUBJECT_LIMIT
    || /[\u0000-\u001f\u007f]/u.test(issuer)
    || /[\u0000-\u001f\u007f]/u.test(subject)) return null;
  return createHmac("sha256", secret)
    .update("tsrp-browser-research-storage-v1\0", "utf8")
    .update(issuer, "utf8")
    .update("\0", "utf8")
    .update(subject, "utf8")
    .digest("hex")
    .slice(0, 32);
}
