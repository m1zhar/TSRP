export function devApiOrigin(value: string | undefined): string {
  const candidate = value?.trim() || "http://127.0.0.1:8000";
  let origin: URL;
  try {
    origin = new URL(candidate);
  } catch {
    throw new TypeError("TSRP_DEV_API_ORIGIN must be a loopback HTTP origin.");
  }
  if (origin.protocol !== "http:"
    || origin.hostname !== "127.0.0.1"
    || origin.username !== ""
    || origin.password !== ""
    || origin.pathname !== "/"
    || origin.search !== ""
    || origin.hash !== "") {
    throw new TypeError("TSRP_DEV_API_ORIGIN must be a loopback HTTP origin.");
  }
  return origin.origin;
}
