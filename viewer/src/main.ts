/**
 * Viewer entry point: a Three.js scene of one study area (ADR 0003).
 *
 * The data is the static scene `mtl-roofs export-scene` writes, attached to a GitHub
 * release and copied next to the viewer by the pages workflow. With no data deployed
 * the viewer says so instead of showing an empty canvas.
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

import { renderFootprint, renderSummary } from "./metrics";
import { loadScene, type Scene } from "./scene";

const DATA_URL = "./data/";

const COLOURS = {
  roof: new THREE.Color("#ece6da"),
  wall: new THREE.Color("#a7a196"),
  reference: new THREE.Color("#e3894a"),
  failed: new THREE.Color("#e5484d"),
  unscored: new THREE.Color("#7d8494"),
  selected: new THREE.Color("#4fb3ff"),
};
/** ASPRS class -> point colour. */
const POINT_COLOURS: Record<number, THREE.Color> = {
  2: new THREE.Color("#5d6372"),
  3: new THREE.Color("#3f7f4a"),
  4: new THREE.Color("#4a9256"),
  5: new THREE.Color("#55a862"),
  6: new THREE.Color("#9db7d8"),
  9: new THREE.Color("#3a6fa8"),
};
/** Error ramp, light (good) to deep orange (bad); RMSE at or above the top stop clamps. */
const RAMP: [number, THREE.Color][] = [
  [0.0, new THREE.Color("#f4f1ea")],
  [0.5, new THREE.Color("#f6c48a")],
  [1.0, new THREE.Color("#ec7a2c")],
  [1.5, new THREE.Color("#b33b0b")],
];

function ramp(v: number): THREE.Color {
  const first = RAMP[0]!;
  if (v <= first[0]) return first[1].clone();
  for (let i = 1; i < RAMP.length; i++) {
    const [b, cb] = RAMP[i]!;
    const [a, ca] = RAMP[i - 1]!;
    if (v <= b) return ca.clone().lerp(cb, (v - a) / (b - a));
  }
  return RAMP[RAMP.length - 1]![1].clone();
}

/** Contiguous triangle range of each owner in an owner-per-triangle array. */
function ranges(owner: Uint16Array): Map<number, [number, number]> {
  const out = new Map<number, [number, number]>();
  for (let t = 0; t < owner.length; t++) {
    const o = owner[t] ?? 0;
    const r = out.get(o);
    if (r === undefined) out.set(o, [t, t + 1]);
    else r[1] = t + 1;
  }
  return out;
}

/** A per-triangle colour function painted into a non-indexed colour buffer. */
function paint(target: Float32Array, nTriangles: number, colour: (t: number) => THREE.Color): void {
  for (let t = 0; t < nTriangles; t++) {
    const c = colour(t);
    for (let v = 0; v < 3; v++) {
      target.set([c.r, c.g, c.b], (t * 3 + v) * 3);
    }
  }
}

interface Layer {
  mesh: THREE.Mesh;
  base: Float32Array;
  error: Float32Array;
  owners: Map<number, [number, number]>;
}

function meshLayer(
  positions: Float32Array,
  owner: Uint16Array,
  base: (t: number) => THREE.Color,
  error: (t: number) => THREE.Color,
  material: THREE.Material,
): Layer {
  const n = owner.length;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const baseColours = new Float32Array(n * 9);
  const errorColours = new Float32Array(n * 9);
  paint(baseColours, n, base);
  paint(errorColours, n, error);
  geometry.setAttribute("color", new THREE.BufferAttribute(baseColours.slice(), 3));
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  return { mesh: new THREE.Mesh(geometry, material), base: baseColours, error: errorColours, owners: ranges(owner) };
}

function build(scene: Scene, canvas: HTMLCanvasElement): void {
  const { index } = scene;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  const world = new THREE.Scene();
  world.background = new THREE.Color("#11131a");
  world.add(new THREE.HemisphereLight(0xffffff, 0x30343f, 1.4));
  const sun = new THREE.DirectionalLight(0xffffff, 1.8);
  sun.position.set(-0.5, 1.0, 0.35);
  world.add(sun);

  const camera = new THREE.PerspectiveCamera(40, 1, 1, 20000);
  const controls = new OrbitControls(camera, canvas);
  {
    // frame the whole area from the south-west, about 40° above the horizon
    const box = new THREE.Box3().setFromBufferAttribute(new THREE.BufferAttribute(scene.rec, 3));
    const centre = box.getCenter(new THREE.Vector3());
    const radius = box.getSize(new THREE.Vector3()).length() / 2;
    const distance = radius / Math.sin(THREE.MathUtils.degToRad(20)) * 0.62;
    controls.target.copy(centre);
    camera.position.copy(centre).add(new THREE.Vector3(-0.45, 0.64, 0.62).normalize().multiplyScalar(distance));
  }
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.48;
  controls.minDistance = 20;
  controls.maxDistance = 4000;

  const fps = index.footprints;
  const refs = index.references;
  const refRmse = new Map<number, number | null>();
  for (const fp of fps) for (const b of fp.buildings) if (b.ref !== null) refRmse.set(b.ref, b.rmse);

  const shaded = (opts: THREE.MeshLambertMaterialParameters = {}): THREE.MeshLambertMaterial =>
    new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide, ...opts });

  const rec = meshLayer(
    scene.rec,
    scene.recOwner,
    (t) => ((scene.recKind[t] ?? 0) === 0 ? COLOURS.roof : COLOURS.wall),
    (t) => {
      const fp = fps[scene.recOwner[t] ?? 0];
      return fp?.rmse == null ? COLOURS.unscored : ramp(fp.rmse);
    },
    shaded(),
  );
  const pad = meshLayer(scene.pad, scene.padOwner, () => COLOURS.failed, () => COLOURS.failed, shaded());
  const ref = meshLayer(
    scene.ref,
    scene.refOwner,
    () => COLOURS.reference,
    (t) => {
      const r = refRmse.get(scene.refOwner[t] ?? 0);
      return r == null ? COLOURS.unscored : ramp(r);
    },
    // drawn slightly in front, so that with both models on the city's roofs win
    shaded({ polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }),
  );
  ref.mesh.visible = false;

  // ground, trees and water as context; building returns separately, because they sit
  // on the roofs and would speckle a model drawn over them
  const BUILDING = 6;
  function pointCloud(keep: (cls: number) => boolean): THREE.Points {
    const idx: number[] = [];
    for (let i = 0; i < scene.pointClass.length; i++) if (keep(scene.pointClass[i] ?? 0)) idx.push(i);
    const pos = new Float32Array(idx.length * 3);
    const col = new Float32Array(idx.length * 3);
    idx.forEach((p, j) => {
      pos.set(scene.points.subarray(p * 3, p * 3 + 3), j * 3);
      const c = POINT_COLOURS[scene.pointClass[p] ?? 2] ?? COLOURS.unscored;
      col.set([c.r, c.g, c.b], j * 3);
    });
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    return new THREE.Points(g, new THREE.PointsMaterial({ size: 0.7, vertexColors: true, sizeAttenuation: true }));
  }
  const context = pointCloud((c) => c !== BUILDING);
  const roofPoints = pointCloud((c) => c === BUILDING);

  world.add(context, roofPoints, rec.mesh, pad.mesh, ref.mesh);

  // --- layers --------------------------------------------------------------------
  let errorMode = false;
  let selected: { layer: Layer; owner: number }[] = [];

  function recolour(layer: Layer): void {
    const attr = layer.mesh.geometry.getAttribute("color") as THREE.BufferAttribute;
    (attr.array as Float32Array).set(errorMode ? layer.error : layer.base);
    for (const s of selected) {
      if (s.layer !== layer) continue;
      const r = layer.owners.get(s.owner);
      if (r === undefined) continue;
      for (let i = r[0] * 9; i < r[1] * 9; i += 3) {
        (attr.array as Float32Array).set([COLOURS.selected.r, COLOURS.selected.g, COLOURS.selected.b], i);
      }
    }
    attr.needsUpdate = true;
  }

  const legend = document.querySelector<HTMLElement>("#legend");
  let pointsOn = true;
  function setLayer(name: string, on: boolean): void {
    if (name === "points") pointsOn = context.visible = on;
    else if (name === "reconstruction") rec.mesh.visible = pad.mesh.visible = on;
    else if (name === "reference") ref.mesh.visible = on;
    else if (name === "error") {
      errorMode = on;
      legend?.classList.toggle("error", on);
      recolour(rec);
      recolour(ref);
    }
    roofPoints.visible = pointsOn && !rec.mesh.visible && !ref.mesh.visible;
  }
  for (const input of document.querySelectorAll<HTMLInputElement>("#layers input[data-layer]")) {
    const name = input.dataset["layer"];
    if (name === undefined) continue;
    input.disabled = false;
    setLayer(name, input.checked);
    input.addEventListener("change", () => setLayer(name, input.checked));
  }

  // --- picking -------------------------------------------------------------------
  const metrics = document.querySelector<HTMLElement>("#metrics");
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let down: [number, number] = [0, 0];
  canvas.addEventListener("pointerdown", (e) => (down = [e.clientX, e.clientY]));
  canvas.addEventListener("pointerup", (e) => {
    if (Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return; // a drag, not a click
    const rect = canvas.getBoundingClientRect();
    pointer.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const layers = [rec, pad, ref].filter((l) => l.mesh.visible);
    const hit = raycaster.intersectObjects(layers.map((l) => l.mesh), false)[0];
    const previous = selected.map((s) => s.layer);
    selected = [];
    let fpIndex: number | null = null;
    let refHit: number | null = null;
    if (hit?.faceIndex != null) {
      const t = hit.faceIndex;
      if (hit.object === rec.mesh) fpIndex = scene.recOwner[t] ?? null;
      else if (hit.object === pad.mesh) fpIndex = scene.padOwner[t] ?? null;
      else {
        refHit = scene.refOwner[t] ?? null;
        fpIndex = refHit === null ? null : (refs[refHit]?.footprint ?? null);
      }
    }
    if (fpIndex !== null) {
      selected.push({ layer: rec, owner: fpIndex }, { layer: pad, owner: fpIndex });
      for (const b of fps[fpIndex]?.buildings ?? []) if (b.ref !== null) selected.push({ layer: ref, owner: b.ref });
    } else if (refHit !== null) {
      selected.push({ layer: ref, owner: refHit });
    }
    for (const layer of new Set([...previous, ...selected.map((s) => s.layer)])) recolour(layer);
    if (metrics !== null) {
      renderFootprint(metrics, fpIndex === null ? null : (fps[fpIndex] ?? null), refHit === null ? null : (refs[refHit] ?? null));
    }
  });

  // --- frame loop ----------------------------------------------------------------
  function resize(): void {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== Math.floor(w * renderer.getPixelRatio()) || canvas.height !== Math.floor(h * renderer.getPixelRatio())) {
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
  }
  renderer.setAnimationLoop(() => {
    resize();
    controls.update();
    renderer.render(world, camera);
  });
}

async function main(): Promise<void> {
  const canvas = document.querySelector<HTMLCanvasElement>("#scene");
  const status = document.querySelector<HTMLElement>("#status");
  const summary = document.querySelector<HTMLElement>("#summary");
  if (canvas === null) return;
  try {
    const scene = await loadScene(DATA_URL, (mb) => {
      if (status !== null) status.textContent = `Loading the scene … ${mb.toFixed(1)} MB`;
    });
    if (summary !== null) renderSummary(summary, scene.index);
    build(scene, canvas);
    status?.remove();
  } catch (err) {
    console.error(err);
    if (status !== null) {
      status.classList.add("error");
      status.textContent = "No scene data in this deployment yet. The results are in the README.";
    }
  }
}

void main();
