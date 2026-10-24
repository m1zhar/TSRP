import test from "node:test";
import assert from "node:assert/strict";
import { devApiOrigin } from "./dev-api-origin.ts";

test("development API proxy defaults to the documented loopback service", () => {
  assert.equal(devApiOrigin(undefined), "http://127.0.0.1:8000");
});

test("development API proxy accepts a loopback HTTP origin with an alternate port", () => {
  assert.equal(devApiOrigin("http://127.0.0.1:8123"), "http://127.0.0.1:8123");
});

test("development API proxy rejects non-loopback, credentialed, or path-based targets", () => {
  for (const value of [
    "https://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.2:8000",
    "http://example.com:8000",
    "http://user:pass@127.0.0.1:8000",
    "http://127.0.0.1:8000/api",
    "http://127.0.0.1:8000/?target=other",
  ]) {
    assert.throws(() => devApiOrigin(value), /loopback HTTP origin/u, value);
  }
});
