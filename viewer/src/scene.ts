/**
 * The static scene written by `mtl-roofs export-scene`: a JSON index and one binary of
 * typed arrays. The format is documented in `src/mtl_roofs/export/scene.py`.
 */

export interface BuildingRow {
  id: string;
  status: string;
  roofType: "flat" | "mixed" | "pitched" | null;
  rmse: number | null;
  coverage: number | null;
  orientation: number | null;
  predFaces: number | null;
  refFaces: number | null;
  temporalOffset: number | null;
  ref: number | null;
}

export interface Footprint {
  id: string;
  status: string;
  reason: string | null;
  points: number | null;
  density: number | null;
  rmse: number | null;
  buildings: BuildingRow[];
}

export interface Reference {
  id: string;
  footprint: number | null;
}

export interface Summary {
  rmse: number | null;
  medianBuildingRmse: number | null;
  coverage: number | null;
  orientationMedian: number | null;
  orientationP90: number | null;
  buildingsReconstructed: number;
  buildings: number;
  footprints: number;
  footprintStatus: Record<string, number>;
}

interface ArrayEntry {
  dtype: "uint8" | "uint16";
  offset: number;
  length: number;
}

export interface SceneIndex {
  version: number;
  area: string;
  runId: string;
  generated: string;
  crs: string;
  origin: [number, number, number];
  scale: number;
  summary: Summary;
  footprints: Footprint[];
  references: Reference[];
  arrays: Record<string, ArrayEntry>;
}

export interface Scene {
  index: SceneIndex;
  /** Positions in metres, already in the viewer's frame: x east, y up, z south. */
  rec: Float32Array;
  recKind: Uint8Array;
  recOwner: Uint16Array;
  pad: Float32Array;
  padOwner: Uint16Array;
  ref: Float32Array;
  refOwner: Uint16Array;
  points: Float32Array;
  pointClass: Uint8Array;
}

function view(buffer: ArrayBuffer, index: SceneIndex, name: string): Uint8Array | Uint16Array {
  const entry = index.arrays[name];
  if (entry === undefined) {
    throw new Error(`scene.bin has no array "${name}"`);
  }
  return entry.dtype === "uint16"
    ? new Uint16Array(buffer, entry.offset, entry.length)
    : new Uint8Array(buffer, entry.offset, entry.length);
}

/**
 * Dequantise EPSG:2950 positions into the viewer's y-up frame, centred on `centre`
 * (in metres from the grid origin) so that float32 keeps centimetres.
 */
function positions(q: Uint16Array, scale: number, centre: [number, number, number]): Float32Array {
  const out = new Float32Array(q.length);
  for (let i = 0; i < q.length; i += 3) {
    const x = (q[i] ?? 0) * scale - centre[0];
    const y = (q[i + 1] ?? 0) * scale - centre[1];
    const z = (q[i + 2] ?? 0) * scale - centre[2];
    out[i] = x;
    out[i + 1] = z;
    out[i + 2] = -y;
  }
  return out;
}

async function fetchWithProgress(url: string, onProgress: (mb: number) => void): Promise<ArrayBuffer> {
  const response = await fetch(url);
  if (!response.ok || response.body === null) {
    throw new Error(`${url}: HTTP ${response.status}`);
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    chunks.push(value);
    received += value.length;
    onProgress(received / 1e6);
  }
  const out = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out.buffer;
}

/** Load and decode the scene under `base` (a directory URL ending in "/"). */
export async function loadScene(base: string, onProgress: (mb: number) => void): Promise<Scene> {
  const response = await fetch(`${base}scene.json`);
  if (!response.ok) {
    throw new Error(`${base}scene.json: HTTP ${response.status}`);
  }
  const index = (await response.json()) as SceneIndex;
  if (index.version !== 1) {
    throw new Error(`unsupported scene version ${index.version}`);
  }
  const buffer = await fetchWithProgress(`${base}scene.bin`, onProgress);

  const rec = view(buffer, index, "rec_pos") as Uint16Array;
  const pts = view(buffer, index, "pts_pos") as Uint16Array;
  // centre on the reconstructed buildings, floor at the lowest point
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, minZ = Infinity;
  for (let i = 0; i < rec.length; i += 3) {
    const x = rec[i] ?? 0, y = rec[i + 1] ?? 0;
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  }
  for (let i = 2; i < pts.length; i += 3) {
    minZ = Math.min(minZ, pts[i] ?? 0);
  }
  const s = index.scale;
  const centre: [number, number, number] = [((minX + maxX) / 2) * s, ((minY + maxY) / 2) * s, minZ * s];

  return {
    index,
    rec: positions(rec, s, centre),
    recKind: view(buffer, index, "rec_kind") as Uint8Array,
    recOwner: view(buffer, index, "rec_owner") as Uint16Array,
    pad: positions(view(buffer, index, "pad_pos") as Uint16Array, s, centre),
    padOwner: view(buffer, index, "pad_owner") as Uint16Array,
    ref: positions(view(buffer, index, "ref_pos") as Uint16Array, s, centre),
    refOwner: view(buffer, index, "ref_owner") as Uint16Array,
    points: positions(pts, s, centre),
    pointClass: view(buffer, index, "pts_class") as Uint8Array,
  };
}
