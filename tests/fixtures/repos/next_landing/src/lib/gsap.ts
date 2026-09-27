export type RevealNode = { style: { opacity: string } };

export function initializeGsapReveal(
  initialize: () => void,
  nodes: readonly RevealNode[],
): void {
  try {
    initialize();
  } catch {
    return;
  }
  for (const node of nodes) {
    node.style.opacity = "1";
  }
}
