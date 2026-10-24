import { createHmac, randomBytes } from "node:crypto";

export const API_ACCESS_TOKEN_TTL_SECONDS = 60;
export const API_ACCESS_TOKEN_ISSUER = "tsrp-web";
export const API_ACCESS_TOKEN_AUDIENCE = "tsrp-api";

export function isStrongServerSecret(value: string | undefined): value is string {
  if (typeof value !== "string" || value !== value.trim()) return false;
  const bytes = Buffer.from(value, "utf8");
  if (bytes.length < 32 || new Set(bytes).size < 12) return false;
  for (let size = 1; size <= 16; size += 1) {
    if (bytes.length % size !== 0) continue;
    if (bytes.every((byte, index) => byte === bytes[index % size])) return false;
  }
  return true;
}

export function issueApiAccessToken(secret: string, now = Math.floor(Date.now() / 1000)) {
  if (!isStrongServerSecret(secret) || !Number.isSafeInteger(now) || now < 0) {
    throw new TypeError("A valid server secret and issuance time are required.");
  }
  const subject = randomBytes(32).toString("hex");
  const encode = (value: object) => Buffer.from(JSON.stringify(value), "utf8").toString("base64url");
  const header = encode({ alg: "HS256", typ: "JWT" });
  const claims = encode({
    iss: API_ACCESS_TOKEN_ISSUER,
    aud: API_ACCESS_TOKEN_AUDIENCE,
    sub: subject,
    iat: now,
    exp: now + API_ACCESS_TOKEN_TTL_SECONDS,
  });
  const signed = `${header}.${claims}`;
  const signature = createHmac("sha256", secret).update(signed, "ascii").digest("base64url");
  return { token: `${signed}.${signature}`, expiresAt: now + API_ACCESS_TOKEN_TTL_SECONDS };
}
