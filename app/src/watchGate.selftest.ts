/** Run: npx --yes tsx src/watchGate.selftest.ts
 * Documents Ready unlock: once playing, buffer dips must not re-arm Preparing.
 */

function assert(cond: unknown, msg: string): void {
  if (!cond) throw new Error(msg);
}

type Gate = "idle" | "preparing" | "ready";

function nextGate(opts: {
  gate: Gate;
  ready: boolean;
  watchUnlocked: boolean;
  jobActive: boolean;
}): Gate {
  const { gate, ready, watchUnlocked, jobActive } = opts;
  if (gate !== "preparing" && gate !== "ready") return gate;
  if (watchUnlocked) return "ready";
  if (ready) return "ready";
  if (jobActive) return "preparing";
  return gate;
}

// Mid-play buffer dip must stay Ready
assert(
  nextGate({ gate: "ready", ready: false, watchUnlocked: true, jobActive: true }) === "ready",
  "unlocked stays ready on buffer dip"
);
// Pre-play dip still Preparing
assert(
  nextGate({ gate: "ready", ready: false, watchUnlocked: false, jobActive: true }) === "preparing",
  "locked re-arms preparing"
);
// First Ready unlock path
assert(
  nextGate({ gate: "preparing", ready: true, watchUnlocked: false, jobActive: true }) === "ready",
  "buffer ready → Ready"
);

console.log("watchGate.selftest OK");
