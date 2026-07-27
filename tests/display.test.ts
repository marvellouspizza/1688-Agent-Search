import assert from "node:assert/strict";
import { test } from "node:test";

import { HermesThinkingSpinner } from "../dist/display.js";

test("non-TTY spinner prints one bounded status line", async () => {
  let text = "";
  const output = {
    isTTY: false,
    write(chunk: string) { text += chunk; return true; },
  };
  const spinner = new HermesThinkingSpinner("thinking...", "dots", output);
  spinner.start();
  await new Promise((resolve) => setTimeout(resolve, 5));
  spinner.stop();
  assert.equal(text, "  [tool] thinking...\n");
});
