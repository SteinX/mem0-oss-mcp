import type PiMemoryClient from "./mem0_oss_pi_client.ts";

type MemoryPage = Awaited<ReturnType<PiMemoryClient["getAll"]>>;

export function formatMemoryPage(page: MemoryPage): string {
  const heading = `Showing ${page.results.length} of ${page.total} indexed memories (preview).`;
  const footer = page.hasMore
    ? `More memories are available. Continue with the same filters and cursor=${JSON.stringify(page.nextCursor)}.`
    : "Cursor traversal is complete; indexed counts can include stale records.";
  const body = page.results.map(memory =>
    `- [mem0:${memory.id}] ${(memory.memory ?? "").replace(/\r?\n/g, " ").slice(0, 160)}`).join("\n");
  const budget = Math.max(0, 9500 - heading.length - footer.length);
  const clipped = body.length > budget;
  return `${heading}\n\n${body.slice(0, budget)}${clipped ? "\n[Preview clipped; repeat this page with a smaller page_size to inspect all IDs.]" : ""}\n\n${footer}`;
}
