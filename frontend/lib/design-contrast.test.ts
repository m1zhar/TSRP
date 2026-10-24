import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");

function token(name: string): string {
  const match = css.match(new RegExp(`--${name}:\\s*(#[0-9A-Fa-f]{6})\\s*;`));
  assert.ok(match, `Missing color token --${name}`);
  return match[1];
}

function luminance(hex: string): number {
  const [red, green, blue] = hex.slice(1).match(/../g)!.map((channel) => {
    const value = Number.parseInt(channel, 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrast(foreground: string, background: string): number {
  const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

test("body and semantic text colors meet WCAG AA on the app's warm surfaces", () => {
  const textTokens = ["ink", "ink-muted", "ink-soft", "accent", "positive", "negative", "warning"];
  const surfaceTokens = ["page", "surface", "surface-subtle", "positive-bg", "negative-bg", "warning-bg"];

  for (const foregroundName of textTokens) {
    for (const backgroundName of surfaceTokens) {
      const ratio = contrast(token(foregroundName), token(backgroundName));
      assert.ok(
        ratio >= 4.5,
        `--${foregroundName} on --${backgroundName} is ${ratio.toFixed(2)}:1; expected at least 4.5:1`,
      );
    }
  }
});

test("mobile research navigation wraps all section links instead of clipping them", () => {
  const mobileBlock = [...css.matchAll(/@media \(max-width: 680px\)\s*\{/gu)]
    .map((match) => {
      const start = match.index ?? -1;
      const end = css.indexOf("\n}", start);
      return end < 0 ? "" : css.slice(start, end);
    })
    .find((block) => /\.research-nav\s*\{[^}]*flex-wrap:\s*wrap/u.test(block));
  assert.ok(mobileBlock, "Missing mobile research navigation breakpoint");
  const navRule = mobileBlock.match(/\.research-nav\s*\{([^}]*)\}/u)?.[1];
  assert.ok(navRule, "Missing mobile research navigation rule");
  assert.match(navRule, /justify-content:\s*center/u);
  assert.match(navRule, /flex-wrap:\s*wrap/u);
  assert.match(navRule, /overflow:\s*visible/u);
});

test("compact mobile valuation actions stay centered when stacked", () => {
  const mobileBlock = [...css.matchAll(/@media \(max-width: 430px\)\s*\{/gu)]
    .map((match) => {
      const start = match.index ?? -1;
      const end = css.indexOf("\n}", start);
      return end < 0 ? "" : css.slice(start, end);
    })
    .find((block) => /\.assumption-actions\s*\{[^}]*flex-direction:\s*column/u.test(block));
  assert.ok(mobileBlock, "Missing stacked mobile valuation action layout");
  const actionRule = mobileBlock.match(/\.assumption-actions\s*\{([^}]*)\}/u)?.[1];
  assert.ok(actionRule, "Missing mobile valuation action rule");
  assert.match(actionRule, /align-items:\s*center/u);
});
