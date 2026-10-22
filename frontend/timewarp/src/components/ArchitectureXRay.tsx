// Architecture X-Ray mode (presenter stage, P6-5, §2g).
//
// A static inline SVG of the component/seam diagram (Gateway, A2UI edge, extraction seam,
// scheduler/clock, portal store). Components "light up" (accent stroke + data-active) driven
// by the most recent time-warp fired events / lifecycle — a small active-set derived from
// state, no diagramming lib.

import { Box, Typography } from "@mui/material";
import { Panel } from "@docubridge/shared";
import { latestLifecycle, type TimewarpState } from "../domain/timewarp";

interface Node {
  id: string;
  label: string;
  x: number;
}

const NODES: Node[] = [
  { id: "gateway", label: "Gateway", x: 20 },
  { id: "edge", label: "A2UI edge", x: 170 },
  { id: "extraction", label: "Extraction seam", x: 320 },
  { id: "scheduler", label: "Scheduler / clock", x: 480 },
  { id: "store", label: "Portal store", x: 640 },
];

// Which components are "hot" at each lifecycle stage (the clock drives them, the store records).
function activeSet(state: TimewarpState | null): Set<string> {
  if (!state) return new Set();
  const life = latestLifecycle(state);
  const active = new Set<string>();
  if (state.exchanges.length > 0) active.add("scheduler"); // an armed ladder lives on the clock
  if (life === "overdue" || life === "reminder") {
    active.add("scheduler");
    active.add("store");
  }
  if (life === "escalated") {
    active.add("scheduler");
    active.add("store");
    active.add("gateway");
  }
  return active;
}

export function ArchitectureXRay({ state }: { state: TimewarpState | null }) {
  const active = activeSet(state);

  return (
    <Panel title="Architecture X-Ray">
      <Typography variant="body2" color="text.secondary" gutterBottom>
        Components light up as the SLA ladder fires (lifecycle {latestLifecycle(state ?? null)}).
      </Typography>
      <Box sx={{ overflowX: "auto" }}>
        <svg width={780} height={140} role="img" aria-label="architecture x-ray" data-testid="xray-svg">
          {/* edges between components */}
          {NODES.slice(0, -1).map((n, i) => (
            <line
              key={`edge-${n.id}`}
              x1={n.x + 120}
              y1={60}
              x2={NODES[i + 1].x}
              y2={60}
              stroke="#bbb"
              strokeWidth={2}
            />
          ))}
          {NODES.map((n) => {
            const isActive = active.has(n.id);
            return (
              <g key={n.id} data-testid={`xray-node-${n.id}`} data-active={isActive ? "true" : "false"}>
                <rect
                  x={n.x}
                  y={30}
                  width={120}
                  height={60}
                  rx={8}
                  fill={isActive ? "#0b728522" : "#fff"}
                  stroke={isActive ? "#0b7285" : "#bbb"}
                  strokeWidth={isActive ? 3 : 1.5}
                />
                <text x={n.x + 60} y={64} textAnchor="middle" fontSize={12} fill="#222">
                  {n.label}
                </text>
              </g>
            );
          })}
        </svg>
      </Box>
    </Panel>
  );
}
