import type { BeforeAgentStartEvent, BeforeAgentStartEventResult, ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { createMemoryLifecycle } from "./src/agent-plugin-core/lifecycle.ts";
import { resolveSearchFilters } from "./src/memory/scoping.ts";
import type { Mem0Config, ScopeContext } from "./src/types.ts";
import { MEMORY_POLICY } from "./src/prompt.ts";
import type PiMemoryClient from "./mem0_oss_pi_client.ts";

type PreviewEvent = BeforeAgentStartEvent & { readonly preview?: boolean };
type RecallOptions = {
  readonly config: Mem0Config;
  readonly scopeCtx: ScopeContext;
  readonly mem0: PiMemoryClient;
  readonly lifecycle: ReturnType<typeof createMemoryLifecycle>;
};

export function registerRecall(pi: ExtensionAPI, options: RecallOptions): void {
  // Senpi accepts the optional third argument; upstream Pi ignores it.
  const onBeforeAgentStart: (
    event: "before_agent_start",
    handler: (event: PreviewEvent, ctx: ExtensionContext) => Promise<BeforeAgentStartEventResult>,
    options?: { readonly previewSafe: boolean },
  ) => void = pi.on.bind(pi);
  onBeforeAgentStart("before_agent_start", async (event) => {
    const systemPrompt = `${event.systemPrompt ?? ""}\n\n${MEMORY_POLICY}`;
    if (event.preview) return { systemPrompt };
    const recall = await options.lifecycle.recall(event.prompt ?? "", options.config.contextInjection,
      (query) => options.mem0.search(query, { filters: resolveSearchFilters("project", options.scopeCtx) }));
    return { systemPrompt, ...(recall ? { message: {
      customType: "mem0-recall", content: recall, display: false,
    } } : {}) };
  }, { previewSafe: true });
}
