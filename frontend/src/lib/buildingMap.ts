import { FLOOR_OUTLINES } from "./floorOutlines";
import type { OccupancyGrid, Position, SceneMap, SimulationState } from "./types";

/**
 * Building walls as the routing grid (frontend test branch, no contract change).
 *
 * The backend's A* plans on `scene_map.occupancy_grid` and treats any non-zero cell as blocked.
 * This turns the generated wall grid of the routing floor (room outlines + perimeter, joined by
 * doors — see scripts/gen-floor-outlines.py) into an OccupancyGrid and publishes it through the
 * existing PUT /map, so recommended and dispatched routes go through doors instead of walls.
 */
export const BUILDING_MAP_PREFIX = "bldg-";

export const isBuildingMap = (map: SceneMap | null | undefined) => !!map?.revision.startsWith(BUILDING_MAP_PREFIX);

/** Cells (col, row) the backend checks for a position: its cell plus neighbours when it sits on a cell boundary. */
function cellsAt(grid: OccupancyGrid, p: Position): [number, number][] {
  const ox = (p.x_m - grid.origin.x_m) / grid.resolution_m;
  const oy = (p.y_m - grid.origin.y_m) / grid.resolution_m;
  if (!(ox >= 0 && ox < grid.width && oy >= 0 && oy < grid.height)) return [];
  const c = Math.floor(ox);
  const r = Math.floor(oy);
  const cols = Number.isInteger(ox) && c > 0 ? [c, c - 1] : [c];
  const rows = Number.isInteger(oy) && r > 0 ? [r, r - 1] : [r];
  return cols.flatMap((cc) => rows.map((rr) => [cc, rr] as [number, number]));
}

function hash(cells: number[]): string {
  let h = 2166136261;
  for (let i = 0; i < cells.length; i++) if (cells[i]) h = Math.imul(h ^ i, 16777619);
  return (h >>> 0).toString(36);
}

/**
 * Scene map whose occupancy grid is the floor's walls. Wall cells under a person are opened so
 * their route can start/end there; the returned `opened` lists who needed that.
 */
export function buildingSceneMap(state: SimulationState, floorId: string): { map: SceneMap; opened: string[] } | null {
  const outline = FLOOR_OUTLINES[floorId];
  if (!outline) return null;
  const frame = state.scenario.frame_id;
  const height = outline.walls.length;
  const width = outline.walls[0].length;
  const cells = new Array<number>(width * height).fill(0);
  // walls[] rows run from the north edge; grid rows advance in +y (from the south edge).
  outline.walls.forEach((line, i) => {
    const row = height - 1 - i;
    for (let c = 0; c < width; c++) if (line[c] === "#") cells[row * width + c] = 100;
  });
  const grid: OccupancyGrid = { origin: { frame_id: frame, x_m: 0, y_m: 0 }, resolution_m: outline.cell_m, width, height, cells };
  const opened = openUnderPeople(state, cells, grid);
  return {
    map: { frame_id: frame, revision: `${BUILDING_MAP_PREFIX}${floorId}-${hash(cells)}`, obstacles: [], occupancy_grid: grid },
    opened,
  };
}

function openUnderPeople(state: SimulationState, cells: number[], grid: OccupancyGrid): string[] {
  const opened: string[] = [];
  for (const e of [...state.scenario.casualties, ...state.scenario.responders]) {
    let hit = false;
    for (const [c, r] of cellsAt(grid, e.position)) {
      if (cells[r * grid.width + c] !== 0) {
        cells[r * grid.width + c] = 0;
        hit = true;
      }
    }
    if (hit) opened.push(e.id);
  }
  return opened;
}

/**
 * The live building map with wall cells under people opened (someone was marked or detected on a
 * wall line after the map was published). Keeps operator "Block route" edits. Null if nothing to open.
 */
export function reopenUnderPeople(state: SimulationState, map: SceneMap): { map: SceneMap; opened: string[] } | null {
  const grid = map.occupancy_grid;
  if (!grid) return null;
  const cells = grid.cells.slice();
  const opened = openUnderPeople(state, cells, grid);
  if (!opened.length) return null;
  const base = map.revision.replace(/-p-[a-z0-9]+$/, "");
  return { map: { ...map, revision: `${base}-p-${hash(cells)}`, occupancy_grid: { ...grid, cells } }, opened };
}
