import assert from "node:assert/strict";
import test from "node:test";
import { initializeGsapReveal } from "../src/lib/gsap.ts";

test("failed initialization restores visible content", () => {
  const node = { style: { opacity: "0" } };
  initializeGsapReveal(() => { throw new Error("Initialization failed"); }, [node]);
  assert.equal(node.style.opacity, "1");
});
