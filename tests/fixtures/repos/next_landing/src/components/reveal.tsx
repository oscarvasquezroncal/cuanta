"use client";

import { useEffect, useRef } from "react";
import { initializeGsapReveal } from "../lib/gsap";

export function Reveal() {
  const node = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (node.current) {
      initializeGsapReveal(() => undefined, [node.current]);
    }
  }, []);
  return <div ref={node} className="reveal">Visible landing content</div>;
}
