import { defineConfig, globalIgnores } from "eslint/config";
import nextPlugin from "@next/eslint-plugin-next";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default defineConfig([
  ...tseslint.configs.recommended,
  nextPlugin.configs["core-web-vitals"],
  jsxA11y.flatConfigs.recommended,
  reactHooks.configs.flat.recommended,
  globalIgnores([".next/**", "node_modules/**", "coverage/**"]),
]);
