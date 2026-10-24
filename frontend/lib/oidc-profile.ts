export function mapOidcProfile(profile: { sub?: unknown }): { id: string } {
  if (typeof profile.sub !== "string" || profile.sub.length === 0 || profile.sub.length > 512 || /[\u0000-\u001f\u007f]/u.test(profile.sub)) {
    throw new Error("The identity provider returned an invalid subject.");
  }
  return { id: profile.sub };
}
