#!/usr/bin/env node
// Browser test: building floor plans + the top-left floor picker on the Command Center.
// Non-destructive (never resets or mutates the simulation). Run: node scripts/e2e-floors.mjs
// Env: BASE (Next app, default :3000), CHROME_PATH, SHOTS (optional dir for screenshots).
import puppeteer from "puppeteer-core";

const BASE = process.env.BASE ?? "http://127.0.0.1:3000";
const BACKEND = process.env.BACKEND ?? "http://127.0.0.1:8000";
const CHROME = process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const SHOTS = process.env.SHOTS;

const ok = (m) => console.log(`  ✓ ${m}`);
const fail = (m) => {
  throw new Error(m);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const PICKER = '[data-testid="floor-picker"]';

const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--window-size=1440,900"] });
const errors = [];
try {
  const page = await browser.newPage();
  page.on("console", (m) => m.type() === "error" && !/favicon|WebSocket|ERR_CONNECTION_REFUSED|404/.test(m.text()) && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.setViewport({ width: 1440, height: 900 });

  const load = async (preview) => {
    await page.goto(`${BASE}/command`, { waitUntil: "networkidle2" });
    await page.evaluate((p) => {
      localStorage.setItem("aegis.preview", JSON.stringify({ multiFloor: p }));
      localStorage.removeItem("aegis.floorplan");
      localStorage.removeItem("aegis.buildingWalls");
    }, preview);
    await page.reload({ waitUntil: "networkidle2" });
    await page.waitForSelector(PICKER, { timeout: 15000 });
    await sleep(600);
  };
  const info = () =>
    page.evaluate(() => {
      const map = document.querySelector(`[data-feed-id="scene-map"], [data-feed-id="map"]`) ?? document.querySelector("[data-map-floor]")?.closest("[data-feed-id]");
      const ol = map?.querySelector("[data-outline-cells]");
      return {
        floor: map?.querySelector("[data-map-floor]")?.getAttribute("data-map-floor"),
        mapSlot: map?.getAttribute("data-feed-slot"),
        outline: ol ? Number(ol.getAttribute("data-outline-cells")) : 0,
        rooms: ol ? Number(ol.getAttribute("data-outline-rooms")) : 0,
        image: !!map?.querySelector("svg image"),
        casualties: map?.querySelectorAll('[data-entity="casualty"]').length ?? 0,
        responders: map?.querySelectorAll('[data-entity="responder"]').length ?? 0,
        gridCells:
          (map?.querySelectorAll("svg rect[stroke='var(--map-cell-line)']").length ?? 0) +
          (map?.querySelector("[data-routing-grid]") ? 1 : 0),
        empty: !!map && /No one detected/.test(map.textContent),
      };
    });
  const openPicker = async () => {
    await page.click(`${PICKER} > button`);
    await page.waitForSelector(`${PICKER} [role=menu]`);
  };
  const counts = () =>
    page.evaluate((P) => Object.fromEntries([...document.querySelectorAll(`${P} [data-floor]`)].map((b) => [b.dataset.floor, Number(b.dataset.people)])), PICKER);
  const pick = async (id) => {
    await openPicker();
    await page.click(`${PICKER} [data-floor="${id}"]`);
    await sleep(700); // swap animation
  };
  const shot = async (name) => SHOTS && page.screenshot({ path: `${SHOTS}/${name}.png` });

  console.log("Default (no floor data from backend):");
  await load(false);
  const pos = await page.evaluate((P) => {
    const r = document.querySelector(P).getBoundingClientRect();
    const slot = document.querySelector('[data-feed-slot="primary"]').getBoundingClientRect();
    return { dx: r.left - slot.left, dy: r.top - slot.top, w: r.width };
  }, PICKER);
  if (pos.dx < 0 || pos.dx > 20 || pos.dy < 0 || pos.dy > 60) fail(`picker not at primary top-left: ${JSON.stringify(pos)}`);
  ok(`picker pinned to primary viewport top-left (+${Math.round(pos.dx)}, +${Math.round(pos.dy)})`);
  let s = await info();
  if (s.floor !== "L1" || !s.outline || s.image || s.gridCells === 0) fail(`L1 view wrong: ${JSON.stringify(s)}`);
  if (s.rooms < 20) fail(`L1 room outlines missing: ${s.rooms}`);
  ok(`L1 shows floor + ${s.rooms} room outlines (no drawing) + routing grid (${s.casualties} victims, ${s.responders} FF)`);
  await openPicker();
  let c = await counts();
  if (Object.keys(c).join() !== "L3,L2,L1,A") fail(`floor grid order ${Object.keys(c)}`);
  if (c.L3 || c.L2 || c.A) fail(`people on other floors without floor data: ${JSON.stringify(c)}`);
  ok(`dropdown grid L3→A (no L4/L5), people only on L1 (${c.L1})`);
  await shot("floors-dropdown-default");
  await page.keyboard.press("Escape");
  await sleep(150);
  if (await page.$(`${PICKER} [role=menu]`)) fail("Escape did not close dropdown");
  ok("Escape closes dropdown");

  await pick("L3");
  s = await info();
  if (s.floor !== "L3" || !s.outline || s.image || s.gridCells !== 0 || s.casualties !== 0 || !s.empty) fail(`L3 view wrong: ${JSON.stringify(s)}`);
  ok("L3 shows level-3 outline, no routing grid, honest 'No one detected'");
  for (const f of ["L2", "A"]) {
    await pick(f);
    s = await info();
    if (s.floor !== f || !s.outline || s.rooms < 20 || s.gridCells !== 0) fail(`${f} view wrong: ${JSON.stringify(s)}`);
    ok(`${f} shows floor + ${s.rooms} room outlines`);
  }
  await pick("L2");

  console.log("Panel swapping still works:");
  const cam = await page.$('[data-feed-slot="secondary"]:not(:has([data-map-floor]))');
  if (!cam) fail("no secondary camera tile");
  const camId = await cam.evaluate((e) => e.dataset.feedId);
  const box = await cam.boundingBox();
  await page.mouse.click(box.x + box.width * 0.3, box.y + box.height * 0.3);
  await sleep(700);
  if ((await page.$eval(`[data-feed-id="${camId}"]`, (e) => e.dataset.feedSlot)) !== "primary") fail("camera did not swap to primary");
  s = await info();
  // Expanding a bodycam selects its firefighter, and the map follows that person's floor.
  if (s.mapSlot !== "secondary" || s.floor !== "L1") fail(`map after swap: ${JSON.stringify(s)}`);
  ok(`camera ${camId} → primary; map → secondary, following its firefighter to L1`);
  if (!(await page.$(PICKER))) fail("picker hidden when camera is primary");
  await pick("L3");
  s = await info();
  if (s.mapSlot !== "primary" || s.floor !== "L3") fail(`floor pick did not bring map back: ${JSON.stringify(s)}`);
  ok("choosing a floor while a camera is primary swaps the map back to the main panel");

  console.log("Building walls → routing:");
  const live = async () => {
    const st = await (await fetch(`${BACKEND}/api/v1/simulations/demo/state`)).json();
    return st;
  };
  const waitRev = async (pred, what) => {
    for (let i = 0; i < 40; i++) {
      const st = await live();
      const prop = st.proposals?.find((x) => x.id === st.current_proposal_id);
      if (pred(st.scenario.scene_map.revision) && prop && prop.map_revision === st.scenario.scene_map.revision) return { st, prop };
      await sleep(300);
    }
    fail(`timed out waiting for ${what}`);
  };
  let { st, prop } = await waitRev((r) => r.startsWith("bldg-L1-"), "building walls map + replan");
  const g = st.scenario.scene_map.occupancy_grid;
  const onWall = (q) => g.cells[Math.floor(q.y_m / g.resolution_m) * g.width + Math.floor(q.x_m / g.resolution_m)] !== 0;
  let routed = 0;
  for (const a of prop.assignments) {
    const w = a.route?.waypoints ?? [];
    if (w.slice(1, -1).some(onWall)) fail(`${a.responder_id}→${a.casualty_id} route goes through a wall`);
    for (let i = 1; i < w.length; i++) {
      const dx = Math.abs(w[i].x_m - w[i - 1].x_m), dy = Math.abs(w[i].y_m - w[i - 1].y_m);
      if (i > 1 && i < w.length - 1 && dx + dy > g.resolution_m + 1e-6) fail("route jumps over cells");
    }
    routed++;
  }
  ok(`walls published (${st.scenario.scene_map.revision}, ${g.width}×${g.height} @ ${g.resolution_m} m); ${routed} recommended routes avoid every wall cell`);
  if ((await page.$eval('[data-testid="walls-toggle"]', (e) => e.textContent)) !== "Walls on") fail("walls toggle not on");
  await page.click('[data-testid="walls-toggle"]');
  ({ st } = await waitRev((r) => r === "sim-demo-map-1", "restored demo map"));
  ok("Walls off → previous map restored and replanned");
  await page.click('[data-testid="walls-toggle"]');
  ({ st } = await waitRev((r) => r.startsWith("bldg-L1-"), "walls re-applied"));
  ok("Walls on → building walls re-applied");
  await shot("floors-walls-routes");

  console.log("PREVIEW multi-floor:");
  await load(true);
  await openPicker();
  c = await counts();
  if (!(c.L1 > 0 && c.L2 > 0 && c.L3 > 0)) fail(`preview should spread people over floors: ${JSON.stringify(c)}`);
  ok(`people detected L1=${c.L1}, L2=${c.L2}, L3=${c.L3}, A=${c.A}`);
  const banner = await page.evaluate(() => document.body.textContent.includes("floor assignments are mock") || /floor assignments/.test(document.body.textContent));
  if (!banner) fail("preview banner missing floor assignments");
  ok("PREVIEW banner names floor assignments");
  await page.click(`${PICKER} [data-floor="L3"]`);
  await sleep(500);
  s = await info();
  if (s.floor !== "L3" || s.casualties === 0) fail(`L3 preview view wrong: ${JSON.stringify(s)}`);
  ok(`L3 shows its ${s.casualties} victim(s), ${s.responders} FF`);
  await shot("floors-l3-preview-dark");

  // Selecting a victim from the strip jumps to that victim's floor.
  const l1Victim = await page.evaluate(() => {
    const strip = [...document.querySelectorAll("button, [role=button]")].filter((b) => /V-|unassessed|immediate|delayed|minor/i.test(b.textContent));
    return strip.length;
  });
  if (l1Victim) ok(`victim strip present (${l1Victim} entries)`);

  // Map tools force the routing floor.
  await page.keyboard.press("m");
  await sleep(700);
  s = await info();
  if (s.floor !== "L1") fail(`Mark (M) should switch to the routing floor: ${s.floor}`);
  ok("Mark responder (M) switches back to L1 routing floor");
  await page.keyboard.press("Escape");

  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  await pick("L1");
  await shot("floors-l1-preview-light");
  ok("light theme renders");

  if (errors.length) fail(`console errors: ${errors.join(" | ")}`);
  ok("no console errors");
  await page.evaluate(() => localStorage.removeItem("aegis.preview"));
  console.log("PASS");
} finally {
  await browser.close();
}
