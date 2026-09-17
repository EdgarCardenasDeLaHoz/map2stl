import { i as is_array, h as get_prototype_of, o as object_prototype, a3 as derived, a4 as attr, a5 as stringify, a6 as ensure_array_like, a7 as attr_class, e as escape_html, a8 as run, a9 as attr_style, aa as head } from "../../chunks/index.js";
import "clsx";
const empty = [];
function snapshot(value, skip_warning = false, no_tojson = false) {
  return clone(value, /* @__PURE__ */ new Map(), "", empty, null, no_tojson);
}
function clone(value, cloned, path, paths, original = null, no_tojson = false) {
  if (typeof value === "object" && value !== null) {
    var unwrapped = cloned.get(value);
    if (unwrapped !== void 0) return unwrapped;
    if (value instanceof Map) return (
      /** @type {Snapshot<T>} */
      new Map(value)
    );
    if (value instanceof Set) return (
      /** @type {Snapshot<T>} */
      new Set(value)
    );
    if (is_array(value)) {
      var copy = (
        /** @type {Snapshot<any>} */
        Array(value.length)
      );
      cloned.set(value, copy);
      if (original !== null) {
        cloned.set(original, copy);
      }
      for (var i = 0; i < value.length; i += 1) {
        var element = value[i];
        if (i in value) {
          copy[i] = clone(element, cloned, path, paths, null, no_tojson);
        }
      }
      return copy;
    }
    if (get_prototype_of(value) === object_prototype) {
      copy = {};
      cloned.set(value, copy);
      if (original !== null) {
        cloned.set(original, copy);
      }
      for (var key of Object.keys(value)) {
        copy[key] = clone(
          // @ts-expect-error
          value[key],
          cloned,
          path,
          paths,
          null,
          no_tojson
        );
      }
      return copy;
    }
    if (value instanceof Date) {
      return (
        /** @type {Snapshot<T>} */
        structuredClone(value)
      );
    }
    if (typeof /** @type {T & { toJSON?: any } } */
    value.toJSON === "function" && !no_tojson) {
      return clone(
        /** @type {T & { toJSON(): any } } */
        value.toJSON(),
        cloned,
        path,
        paths,
        // Associate the instance with the toJSON clone
        value
      );
    }
  }
  if (value instanceof EventTarget) {
    return (
      /** @type {Snapshot<T>} */
      value
    );
  }
  try {
    return (
      /** @type {Snapshot<T>} */
      structuredClone(value)
    );
  } catch (e) {
    return (
      /** @type {Snapshot<T>} */
      value
    );
  }
}
async function request(path, init) {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers ?? {} }
  });
  if (!response.ok) {
    throw new Error(await describeFailure(response));
  }
  return await response.json();
}
async function describeFailure(response) {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail) && body.detail.length) {
      const first = body.detail[0];
      const field = Array.isArray(first.loc) ? first.loc.slice(1).join(".") : "";
      return field ? `${field}: ${first.msg}` : first.msg;
    }
  } catch {
  }
  return `${response.status} ${response.statusText}`;
}
const api = {
  regions: () => request("/api/regions").then((r) => r.regions),
  settings: (name) => request(`/api/regions/${encodeURIComponent(name)}/settings`),
  saveSettings: (name, settings) => request(`/api/regions/${encodeURIComponent(name)}/settings`, {
    method: "PUT",
    body: JSON.stringify(settings)
  }),
  sources: () => request(
    "/api/sources"
  ),
  fetchDem: (bbox, dem, projection) => request("/api/dem", {
    method: "POST",
    body: JSON.stringify({ bbox, dem, projection })
  }),
  overlay: (bbox, kind, dim) => request("/api/overlay", {
    method: "POST",
    body: JSON.stringify({ bbox, kind, dim })
  }),
  /** Elevation as raw float32 for the 3D view. Bytes, not base64 in JSON. */
  elevation: async (demId) => {
    const response = await fetch(`/api/dem/${demId}/elevation.bin`);
    if (!response.ok) throw new Error(await describeFailure(response));
    return new Float32Array(await response.arrayBuffer());
  },
  previewUrl: (demId, colormap = "terrain") => `/api/dem/${demId}/preview.png?colormap=${encodeURIComponent(colormap)}`,
  startExport: (demId, model, exp, name) => request("/api/export", {
    method: "POST",
    body: JSON.stringify({ demId, model, export: exp, name })
  }),
  cancelJob: (jobId) => request(`/api/jobs/${jobId}`, { method: "DELETE" }),
  downloadUrl: (jobId) => `/api/jobs/${jobId}/download`,
  /**
   * Subscribe to a job's progress. Returns an unsubscribe function.
   *
   * The server pushes one event per real change. v1 polled every 250 ms for the whole
   * render, which for a two-minute export is roughly 480 requests to observe about eight
   * distinct states.
   */
  watchJob(jobId, onUpdate, onError) {
    const source = new EventSource(`/api/jobs/${jobId}/events`);
    let finished = false;
    source.onmessage = (event) => {
      const snapshot2 = JSON.parse(event.data);
      onUpdate(snapshot2);
      if (snapshot2.state !== "running") {
        finished = true;
        source.close();
      }
    };
    source.onerror = () => {
      source.close();
      if (!finished) onError("Lost contact with the export. It may still be running.");
    };
    return () => {
      finished = true;
      source.close();
    };
  }
};
function defaultSettings() {
  return {
    schemaVersion: 1,
    dem: {
      source: "local",
      dim: 600,
      depthScale: 0.5,
      waterScale: 0.05,
      subtractWater: true,
      maintainDimensions: false
    },
    projection: { name: "none", clipValidRegion: false },
    overlays: { satellite: false, waterMask: false, landCover: false },
    model: {
      modelHeight: 25,
      baseHeight: 5,
      exaggeration: 1,
      mmPerPixel: 1,
      seaLevelCap: false
    },
    export: { format: "stl", engraveLabel: false, labelText: "" }
  };
}
class AppState {
  regions = [];
  sources = [];
  openTopoKeyConfigured = false;
  region = null;
  settings = defaultSettings();
  dem = null;
  elevation = null;
  overlayImages = {};
  job = null;
  notices = [];
  loadError = null;
  dirty = false;
  saving = false;
  regionFilter = "";
  #noticeSeq = 0;
  #stopWatching = null;
  #saveTimer = null;
  /** Guards the auto-save so that loading a region's settings does not immediately re-save them. */
  #applying = false;
  #stage = derived(
    // -- derived ---------------------------------------------------------
    () => {
      if (!this.region) return "no-region";
      if (this.job?.state === "running") return "exporting";
      if (this.job?.state === "done") return "exported";
      if (this.dem) return "loaded";
      if (this.loadError) return "ready-to-load";
      return "ready-to-load";
    }
  );
  get stage() {
    return this.#stage();
  }
  set stage($$value) {
    return this.#stage($$value);
  }
  loading = false;
  #visibleRegions = derived(() => {
    const needle = this.regionFilter.trim().toLowerCase();
    if (!needle) return this.regions;
    return this.regions.filter((r) => r.name.toLowerCase().includes(needle) || (r.label ?? "").toLowerCase().includes(needle) || (r.description ?? "").toLowerCase().includes(needle));
  });
  get visibleRegions() {
    return this.#visibleRegions();
  }
  set visibleRegions($$value) {
    return this.#visibleRegions($$value);
  }
  #projection = derived(() => {
    if (!this.dem) return null;
    const { width, height } = this.dem;
    const { mmPerPixel, modelHeight, baseHeight } = this.settings.model;
    return {
      gridWidth: width,
      gridHeight: height,
      mmWidth: width * mmPerPixel,
      mmDepth: height * mmPerPixel,
      // Matches the server: the bottom cap sits at z=0 when there is a base, and one
      // millimetre below the surface's lowest point when there is not.
      mmHeight: baseHeight > 0 ? modelHeight + baseHeight : modelHeight + 1,
      // array_to_mesh emits two triangles per grid cell for the surface, plus walls
      // and a floor. The surface term dominates; this is an estimate, and labelled as one.
      estimatedFaces: (width - 1) * (height - 1) * 2
    };
  });
  get projection() {
    return this.#projection();
  }
  set projection($$value) {
    return this.#projection($$value);
  }
  #exportBlockedReason = derived(() => {
    if (!this.region) return "Choose a region first.";
    if (!this.dem) return "Load the terrain first — the mesh is built from it.";
    if (this.stage === "exporting") return "An export is already running.";
    return null;
  });
  get exportBlockedReason() {
    return this.#exportBlockedReason();
  }
  set exportBlockedReason($$value) {
    return this.#exportBlockedReason($$value);
  }
  async init() {
    try {
      const [regions, sources] = await Promise.all([api.regions(), api.sources()]);
      this.regions = regions;
      this.sources = sources.sources;
      this.openTopoKeyConfigured = sources.openTopoKeyConfigured;
    } catch (err) {
      this.notify("error", `Could not reach the server: ${message(err)}`);
    }
  }
  async selectRegion(region) {
    this.region = region;
    this.dem = null;
    this.elevation = null;
    this.overlayImages = {};
    this.job = null;
    this.loadError = null;
    this.#applying = true;
    try {
      this.settings = await api.settings(region.name);
      this.#useAvailableSource();
    } catch (err) {
      this.settings = defaultSettings();
      this.notify("error", `Could not load saved settings: ${message(err)}`);
    } finally {
      setTimeout(
        () => {
          this.#applying = false;
          this.dirty = false;
        },
        0
      );
    }
  }
  /**
   * Swap a saved source that this machine cannot serve for one it can.
   *
   * Settings outlive the machine they were saved on: a region saved when the SRTM tile
   * store was present still names it after the store is gone. v1 kept the dead source
   * selected and let the fetch return an array of zeros, which exported as a flat slab.
   */
  #useAvailableSource() {
    if (!this.sources.length) return;
    const chosen = this.sources.find((s) => s.id === this.settings.dem.source);
    if (chosen?.available) return;
    const fallback = this.sources.find((s) => s.available);
    if (!fallback) {
      this.notify("error", "No elevation source is available on this machine.");
      return;
    }
    const was = chosen?.label ?? this.settings.dem.source;
    this.settings.dem.source = fallback.id;
    this.notify("info", `${was} is unavailable here — using ${fallback.label} instead.`);
  }
  async loadTerrain() {
    if (!this.region) return;
    this.loading = true;
    this.loadError = null;
    try {
      this.dem = await api.fetchDem(this.region.bbox, this.settings.dem, this.settings.projection);
      this.elevation = null;
      this.notify("success", `Terrain loaded: ${this.dem.width}x${this.dem.height} from ${this.dem.sourceUsed} in ${this.dem.fetchSeconds.toFixed(2)}s`);
      void this.refreshOverlays();
    } catch (err) {
      this.loadError = message(err);
      this.notify("error", this.loadError);
    } finally {
      this.loading = false;
    }
  }
  async refreshOverlays() {
    if (!this.region) return;
    const wanted = ["satellite", "waterMask", "landCover"].filter((k) => this.settings.overlays[k]);
    for (const kind of wanted) {
      if (this.overlayImages[kind]) continue;
      try {
        const result = await api.overlay(this.region.bbox, kind, this.settings.dem.dim);
        this.overlayImages = {
          ...this.overlayImages,
          [kind]: `data:${result.mime};base64,${result.dataBase64}`
        };
      } catch (err) {
        this.settings.overlays[kind] = false;
        this.notify("error", message(err));
      }
    }
  }
  async ensureElevation() {
    if (!this.dem) return null;
    if (this.elevation) return this.elevation;
    try {
      this.elevation = await api.elevation(this.dem.demId);
      return this.elevation;
    } catch (err) {
      this.notify("error", `Could not load elevation data: ${message(err)}`);
      return null;
    }
  }
  async startExport() {
    if (!this.dem || !this.region) return;
    this.#stopWatching?.();
    try {
      this.job = await api.startExport(this.dem.demId, this.settings.model, this.settings.export, this.region.name);
      this.#stopWatching = api.watchJob(
        this.job.jobId,
        (snapshot2) => {
          this.job = snapshot2;
          if (snapshot2.state === "done") {
            this.notify("success", `Export ready — ${snapshot2.result.faceCount?.toLocaleString()} faces`);
          } else if (snapshot2.state === "failed") {
            this.notify("error", snapshot2.error ?? "The export failed.");
          } else if (snapshot2.state === "cancelled") {
            this.notify("info", "Export cancelled.");
          }
        },
        (err) => this.notify("error", err)
      );
    } catch (err) {
      this.notify("error", message(err));
    }
  }
  async cancelExport() {
    if (!this.job || this.job.state !== "running") return;
    try {
      await api.cancelJob(this.job.jobId);
    } catch (err) {
      this.notify("error", message(err));
    }
  }
  download() {
    if (this.job?.state !== "done") return;
    window.location.href = api.downloadUrl(this.job.jobId);
  }
  // -- settings persistence --------------------------------------------
  /** Called by controls after any edit. Debounced; the indicator updates immediately. */
  touch() {
    if (this.#applying) return;
    this.dirty = true;
    if (this.#saveTimer) clearTimeout(this.#saveTimer);
    this.#saveTimer = setTimeout(() => void this.save(), 1200);
  }
  async save() {
    if (!this.region || !this.dirty) return;
    this.saving = true;
    try {
      await api.saveSettings(this.region.name, snapshot(this.settings));
      this.dirty = false;
    } catch (err) {
      this.notify("error", `Could not save settings: ${message(err)}`);
    } finally {
      this.saving = false;
    }
  }
  /** Editing anything that feeds the fetch invalidates the loaded terrain. */
  invalidateTerrain() {
    if (this.dem) {
      this.dem = null;
      this.elevation = null;
      this.overlayImages = {};
      this.job = null;
      this.notify("info", "Those settings change the terrain — load it again.");
    }
    this.touch();
  }
  // -- notices ---------------------------------------------------------
  notify(kind, text) {
    const notice = { id: ++this.#noticeSeq, kind, text };
    this.notices = [...this.notices, notice];
    if (kind !== "error") {
      setTimeout(() => this.dismiss(notice.id), 4e3);
    }
  }
  dismiss(id) {
    this.notices = this.notices.filter((n) => n.id !== id);
  }
}
function message(err) {
  return err instanceof Error ? err.message : String(err);
}
const app = new AppState();
function RegionList($$renderer, $$props) {
  $$renderer.component(($$renderer2) => {
    function span(region) {
      const ns = region.bbox.north - region.bbox.south;
      const ew = region.bbox.east - region.bbox.west;
      return `${ns.toFixed(2)}° × ${ew.toFixed(2)}°`;
    }
    $$renderer2.push(`<div class="regions svelte-jk3sbk"><div class="search svelte-jk3sbk"><input type="search"${attr("placeholder", `Filter ${stringify(app.regions.length)} regions`)}${attr("value", app.regionFilter)} aria-label="Filter regions by name" class="svelte-jk3sbk"/></div> <ul class="list svelte-jk3sbk">`);
    const each_array = ensure_array_like(app.visibleRegions);
    if (each_array.length !== 0) {
      $$renderer2.push("<!--[-->");
      for (let $$index = 0, $$length = each_array.length; $$index < $$length; $$index++) {
        let region = each_array[$$index];
        $$renderer2.push(`<li><button type="button"${attr_class("region svelte-jk3sbk", void 0, { "selected": app.region?.name === region.name })}><span class="name svelte-jk3sbk">${escape_html(region.name)}</span> <span class="meta svelte-jk3sbk">${escape_html(span(region))} `);
        if (region.label && region.label !== region.name) {
          $$renderer2.push("<!--[0-->");
          $$renderer2.push(`· ${escape_html(region.label)}`);
        } else {
          $$renderer2.push("<!--[-1-->");
        }
        $$renderer2.push(`<!--]--></span></button></li>`);
      }
    } else {
      $$renderer2.push("<!--[!-->");
      $$renderer2.push(`<li class="empty svelte-jk3sbk">Nothing matches “${escape_html(app.regionFilter)}”.</li>`);
    }
    $$renderer2.push(`<!--]--></ul></div>`);
  });
}
function Field($$renderer, $$props) {
  let { label, hint = "", value = "", children } = $$props;
  $$renderer.push(`<label class="field svelte-175sa6o"><span class="field-label svelte-175sa6o"><span class="field-name svelte-175sa6o">${escape_html(label)}</span> `);
  if (value) {
    $$renderer.push("<!--[0-->");
    $$renderer.push(`<span class="field-value svelte-175sa6o">${escape_html(value)}</span>`);
  } else {
    $$renderer.push("<!--[-1-->");
  }
  $$renderer.push(`<!--]--></span> `);
  children($$renderer);
  $$renderer.push(`<!----> `);
  if (hint) {
    $$renderer.push("<!--[0-->");
    $$renderer.push(`<span class="field-hint svelte-175sa6o">${escape_html(hint)}</span>`);
  } else {
    $$renderer.push("<!--[-1-->");
  }
  $$renderer.push(`<!--]--></label>`);
}
function Section($$renderer, $$props) {
  $$renderer.component(($$renderer2) => {
    let { title, subtitle = "", advanced = false, count = 0, children } = $$props;
    const storageKey = `v2.section.${run(() => title)}`;
    let open = readOpen();
    function readOpen() {
      try {
        const stored = sessionStorage.getItem(storageKey);
        if (stored !== null) return stored === "1";
      } catch {
      }
      return !advanced;
    }
    $$renderer2.push(`<section${attr_class("section svelte-7a8mnf", void 0, { "open": open })}><button type="button" class="section-head svelte-7a8mnf"${attr(
      "aria-expanded",
      // See above — failing to remember is not worth surfacing.
      open
    )}><span class="chevron svelte-7a8mnf" aria-hidden="true">${escape_html(open ? "▾" : "▸")}</span> <span class="section-title svelte-7a8mnf">${escape_html(title)}</span> `);
    if (!open && count) {
      $$renderer2.push("<!--[0-->");
      $$renderer2.push(`<span class="section-count svelte-7a8mnf">${escape_html(count)}</span>`);
    } else {
      $$renderer2.push("<!--[-1-->");
    }
    $$renderer2.push(`<!--]--></button> `);
    if (subtitle && open) {
      $$renderer2.push("<!--[0-->");
      $$renderer2.push(`<p class="section-subtitle svelte-7a8mnf">${escape_html(subtitle)}</p>`);
    } else {
      $$renderer2.push("<!--[-1-->");
    }
    $$renderer2.push(`<!--]--> `);
    if (open) {
      $$renderer2.push("<!--[0-->");
      $$renderer2.push(`<div class="section-body svelte-7a8mnf">`);
      children($$renderer2);
      $$renderer2.push(`<!----></div>`);
    } else {
      $$renderer2.push("<!--[-1-->");
    }
    $$renderer2.push(`<!--]--></section>`);
  });
}
function SettingsPanel($$renderer, $$props) {
  $$renderer.component(($$renderer2) => {
    const PROJECTIONS = [
      { id: "none", label: "None (plate carrée)" },
      { id: "cosine", label: "Cosine correction" },
      { id: "mercator", label: "Web Mercator" },
      { id: "equidistant", label: "Equidistant cylindrical" },
      { id: "lambert", label: "Lambert conformal" },
      { id: "sinusoidal", label: "Sinusoidal" }
    ];
    let s = derived(() => app.settings);
    let selectedSource = derived(() => app.sources.find((x) => x.id === s().dem.source));
    $$renderer2.push(`<div class="panel svelte-d580bl">`);
    Section($$renderer2, {
      title: "Terrain",
      subtitle: "What gets fetched. Changing any of these needs a reload.",
      children: ($$renderer3) => {
        Field($$renderer3, {
          label: "Elevation source",
          hint: selectedSource()?.note ?? "",
          children: ($$renderer4) => {
            $$renderer4.select(
              {
                value: s().dem.source,
                onchange: () => app.invalidateTerrain()
              },
              ($$renderer5) => {
                $$renderer5.push(`<!--[-->`);
                const each_array = ensure_array_like(app.sources);
                for (let $$index = 0, $$length = each_array.length; $$index < $$length; $$index++) {
                  let source = each_array[$$index];
                  $$renderer5.option({ value: source.id, disabled: !source.available }, ($$renderer6) => {
                    $$renderer6.push(`${escape_html(source.label)}${escape_html(source.available ? "" : " — unavailable")}`);
                  });
                }
                $$renderer5.push(`<!--]-->`);
              }
            );
          }
        });
        $$renderer3.push(`<!----> `);
        Field($$renderer3, {
          label: "Resolution",
          value: `${stringify(s().dem.dim)} px`,
          hint: "Grid width. Cost rises with the square.",
          children: ($$renderer4) => {
            $$renderer4.push(`<input type="range" min="100" max="1600" step="50"${attr("value", s().dem.dim)}/>`);
          }
        });
        $$renderer3.push(`<!----> `);
        Field($$renderer3, {
          label: "Subtract water",
          children: ($$renderer4) => {
            $$renderer4.push(`<label class="check svelte-d580bl"><input type="checkbox"${attr("checked", s().dem.subtractWater, true)} class="svelte-d580bl"/> <span>Cut lakes and sea below the surface</span></label>`);
          }
        });
        $$renderer3.push(`<!---->`);
      }
    });
    $$renderer2.push(`<!----> `);
    Section($$renderer2, {
      title: "Model",
      subtitle: "How the elevation grid becomes millimetres.",
      children: ($$renderer3) => {
        Field($$renderer3, {
          label: "Model height",
          value: `${stringify(s().model.modelHeight)} mm`,
          children: ($$renderer4) => {
            $$renderer4.push(`<input type="range" min="2" max="120" step="1"${attr("value", s().model.modelHeight)}/>`);
          }
        });
        $$renderer3.push(`<!----> `);
        Field($$renderer3, {
          label: "Base thickness",
          value: `${stringify(s().model.baseHeight)} mm`,
          hint: "Solid plate under the terrain.",
          children: ($$renderer4) => {
            $$renderer4.push(`<input type="range" min="0" max="30" step="0.5"${attr("value", s().model.baseHeight)}/>`);
          }
        });
        $$renderer3.push(`<!----> `);
        Field($$renderer3, {
          label: "Vertical exaggeration",
          value: `${stringify(s().model.exaggeration)}x`,
          hint: "Applied before the height is normalised, so it changes the shape of the relief, not its size.",
          children: ($$renderer4) => {
            $$renderer4.push(`<input type="range" min="0.25" max="8" step="0.25"${attr("value", s().model.exaggeration)}/>`);
          }
        });
        $$renderer3.push(`<!----> `);
        Field($$renderer3, {
          label: "Scale",
          value: `${stringify(s().model.mmPerPixel)} mm/px`,
          children: ($$renderer4) => {
            $$renderer4.push(`<input type="range" min="0.1" max="4" step="0.1"${attr("value", s().model.mmPerPixel)}/>`);
          }
        });
        $$renderer3.push(`<!----> `);
        Field($$renderer3, {
          label: "Sea-level cap",
          children: ($$renderer4) => {
            $$renderer4.push(`<label class="check svelte-d580bl"><input type="checkbox"${attr("checked", s().model.seaLevelCap, true)} class="svelte-d580bl"/> <span>Flatten everything below sea level to zero</span></label>`);
          }
        });
        $$renderer3.push(`<!---->`);
      }
    });
    $$renderer2.push(`<!----> `);
    Section($$renderer2, {
      title: "Projection",
      advanced: true,
      count: 2,
      subtitle: "Reshapes the fetched grid. Cheap — no refetch.",
      children: ($$renderer3) => {
        Field($$renderer3, {
          label: "Projection",
          children: ($$renderer4) => {
            $$renderer4.select(
              {
                value: s().projection.name,
                onchange: () => app.invalidateTerrain()
              },
              ($$renderer5) => {
                $$renderer5.push(`<!--[-->`);
                const each_array_1 = ensure_array_like(PROJECTIONS);
                for (let $$index_1 = 0, $$length = each_array_1.length; $$index_1 < $$length; $$index_1++) {
                  let projection = each_array_1[$$index_1];
                  $$renderer5.option({ value: projection.id }, ($$renderer6) => {
                    $$renderer6.push(`${escape_html(projection.label)}`);
                  });
                }
                $$renderer5.push(`<!--]-->`);
              }
            );
          }
        });
        $$renderer3.push(`<!----> `);
        Field($$renderer3, {
          label: "Clip invalid edges",
          hint: "Trim the empty rows and columns a projection leaves behind.",
          children: ($$renderer4) => {
            $$renderer4.push(`<label class="check svelte-d580bl"><input type="checkbox"${attr("checked", s().projection.clipValidRegion, true)} class="svelte-d580bl"/> <span>Crop to the valid region</span></label>`);
          }
        });
        $$renderer3.push(`<!---->`);
      }
    });
    $$renderer2.push(`<!----> `);
    Section($$renderer2, {
      title: "Overlays",
      advanced: true,
      count: 3,
      subtitle: "Preview imagery only. None of it reaches the mesh.",
      children: ($$renderer3) => {
        Field($$renderer3, {
          label: "Satellite",
          children: ($$renderer4) => {
            $$renderer4.push(`<label class="check svelte-d580bl"><input type="checkbox"${attr("checked", s().overlays.satellite, true)} class="svelte-d580bl"/> <span>ESRI world imagery</span></label>`);
          }
        });
        $$renderer3.push(`<!----> `);
        Field($$renderer3, {
          label: "Water mask",
          children: ($$renderer4) => {
            $$renderer4.push(`<label class="check svelte-d580bl"><input type="checkbox"${attr("checked", s().overlays.waterMask, true)} class="svelte-d580bl"/> <span>Surface water from ESA WorldCover</span></label>`);
          }
        });
        $$renderer3.push(`<!----> `);
        Field($$renderer3, {
          label: "Land cover",
          children: ($$renderer4) => {
            $$renderer4.push(`<label class="check svelte-d580bl"><input type="checkbox"${attr("checked", s().overlays.landCover, true)} class="svelte-d580bl"/> <span>ESA WorldCover classes</span></label>`);
          }
        });
        $$renderer3.push(`<!---->`);
      }
    });
    $$renderer2.push(`<!----> `);
    Section($$renderer2, {
      title: "Export options",
      advanced: true,
      count: 3,
      children: ($$renderer3) => {
        Field($$renderer3, {
          label: "Format",
          children: ($$renderer4) => {
            $$renderer4.select({ value: s().export.format, onchange: () => app.touch() }, ($$renderer5) => {
              $$renderer5.option({ value: "stl" }, ($$renderer6) => {
                $$renderer6.push(`STL — printing`);
              });
              $$renderer5.option({ value: "obj" }, ($$renderer6) => {
                $$renderer6.push(`OBJ — editing`);
              });
              $$renderer5.option({ value: "3mf" }, ($$renderer6) => {
                $$renderer6.push(`3MF — printing with metadata`);
              });
            });
          }
        });
        $$renderer3.push(`<!----> `);
        Field($$renderer3, {
          label: "Engrave a label",
          children: ($$renderer4) => {
            $$renderer4.push(`<label class="check svelte-d580bl"><input type="checkbox"${attr("checked", s().export.engraveLabel, true)} class="svelte-d580bl"/> <span>Sink text into the front edge</span></label>`);
          }
        });
        $$renderer3.push(`<!----> `);
        if (s().export.engraveLabel) {
          $$renderer3.push("<!--[0-->");
          Field($$renderer3, {
            label: "Label text",
            children: ($$renderer4) => {
              $$renderer4.push(`<input type="text"${attr("value", s().export.labelText)}${attr("placeholder", app.region?.label ?? app.region?.name ?? "Region name")}/>`);
            }
          });
        } else {
          $$renderer3.push("<!--[-1-->");
        }
        $$renderer3.push(`<!--]-->`);
      }
    });
    $$renderer2.push(`<!----></div>`);
  });
}
function MapView($$renderer, $$props) {
  $$renderer.component(($$renderer2) => {
    $$renderer2.push(`<div class="map svelte-njbu1f"></div>`);
  });
}
function ExportBar($$renderer, $$props) {
  $$renderer.component(($$renderer2) => {
    let job = derived(() => app.job);
    let projection = derived(() => app.projection);
    let blocked = derived(() => app.exportBlockedReason);
    $$renderer2.push(`<div class="bar svelte-1y6z5jn"><div class="actions svelte-1y6z5jn"><button type="button" class="primary svelte-1y6z5jn"${attr("disabled", !app.region || app.loading, true)}>${escape_html(app.loading ? "Loading terrain…" : app.dem ? "Reload terrain" : "Load terrain")}</button> <button type="button" class="primary accent svelte-1y6z5jn"${attr("disabled", blocked() !== null, true)}${attr("title", blocked() ?? "")}>Export ${escape_html(app.settings.export.format.toUpperCase())}</button> `);
    if (job()?.state === "running") {
      $$renderer2.push("<!--[0-->");
      $$renderer2.push(`<button type="button" class="svelte-1y6z5jn">Cancel</button>`);
    } else {
      $$renderer2.push("<!--[-1-->");
    }
    $$renderer2.push(`<!--]--> `);
    if (job()?.state === "done") {
      $$renderer2.push("<!--[0-->");
      $$renderer2.push(`<button type="button" class="primary accent svelte-1y6z5jn">Download</button>`);
    } else {
      $$renderer2.push("<!--[-1-->");
    }
    $$renderer2.push(`<!--]--></div> <div class="status svelte-1y6z5jn">`);
    if (job()?.state === "running") {
      $$renderer2.push("<!--[0-->");
      $$renderer2.push(`<div class="progress svelte-1y6z5jn" role="progressbar"${attr("aria-valuenow", job().percent)}><div class="fill svelte-1y6z5jn"${attr_style(`width: ${stringify(job().percent)}%`)}></div></div> <span class="line svelte-1y6z5jn">${escape_html(job().message)} — ${escape_html(job().percent)}% (${escape_html(job().elapsedSeconds.toFixed(1))} s)</span>`);
    } else if (job()?.state === "done" && job().result.faceCount) {
      $$renderer2.push("<!--[1-->");
      $$renderer2.push(`<span class="line svelte-1y6z5jn">${escape_html(job().result.faceCount.toLocaleString())} faces ·
				${escape_html(job().result.watertight ? "watertight" : "NOT watertight")} ·
				${escape_html(job().result.sizeMm?.x.toFixed(0))} x ${escape_html(job().result.sizeMm?.y.toFixed(0))} x
				${escape_html(job().result.sizeMm?.z.toFixed(0))} mm</span>`);
    } else if (job()?.state === "failed") {
      $$renderer2.push("<!--[2-->");
      $$renderer2.push(`<span class="line error svelte-1y6z5jn">${escape_html(job().error)}</span>`);
    } else if (blocked()) {
      $$renderer2.push("<!--[3-->");
      $$renderer2.push(`<span class="line dim svelte-1y6z5jn">${escape_html(blocked())}</span>`);
    } else if (projection()) {
      $$renderer2.push("<!--[4-->");
      $$renderer2.push(`<span class="line dim svelte-1y6z5jn">Will produce ${escape_html(projection().mmWidth.toFixed(0))} x ${escape_html(projection().mmDepth.toFixed(0))} x
				${escape_html(projection().mmHeight.toFixed(0))} mm, about
				${escape_html(projection().estimatedFaces.toLocaleString())} faces</span>`);
    } else {
      $$renderer2.push("<!--[-1-->");
    }
    $$renderer2.push(`<!--]--></div> <div class="save svelte-1y6z5jn">`);
    if (app.saving) {
      $$renderer2.push("<!--[0-->");
      $$renderer2.push(`<span class="dim svelte-1y6z5jn">Saving…</span>`);
    } else if (app.dirty) {
      $$renderer2.push("<!--[1-->");
      $$renderer2.push(`<span class="dim svelte-1y6z5jn">Unsaved</span>`);
    } else if (app.region) {
      $$renderer2.push("<!--[2-->");
      $$renderer2.push(`<span class="dim svelte-1y6z5jn">Saved</span>`);
    } else {
      $$renderer2.push("<!--[-1-->");
    }
    $$renderer2.push(`<!--]--></div></div>`);
  });
}
function Notices($$renderer, $$props) {
  $$renderer.component(($$renderer2) => {
    $$renderer2.push(`<div class="notices svelte-1pqi0mh" aria-live="polite"><!--[-->`);
    const each_array = ensure_array_like(
      /**
       * Transient messages, stacked in a corner.
       *
       * Errors have no timer — they stay until dismissed. v1 faded every message, failures
       * included, after two seconds, so an export that died while the user looked at something
       * else left no evidence that it had ever run.
       */
      app.notices
    );
    for (let $$index = 0, $$length = each_array.length; $$index < $$length; $$index++) {
      let notice = each_array[$$index];
      $$renderer2.push(`<div${attr_class(`notice ${stringify(notice.kind)}`, "svelte-1pqi0mh")}><span class="text svelte-1pqi0mh">${escape_html(notice.text)}</span> <button type="button" aria-label="Dismiss" class="svelte-1pqi0mh">×</button></div>`);
    }
    $$renderer2.push(`<!--]--></div>`);
  });
}
function _page($$renderer, $$props) {
  $$renderer.component(($$renderer2) => {
    let tab = "map";
    const TABS = [
      { id: "map", label: "Map" },
      { id: "terrain", label: "Terrain" },
      { id: "model", label: "3D" }
    ];
    head("1uha8ag", $$renderer2, ($$renderer3) => {
      $$renderer3.title(($$renderer4) => {
        $$renderer4.push(`<title>strm2stl v2</title>`);
      });
    });
    $$renderer2.push(`<div class="app svelte-1uha8ag"><header class="svelte-1uha8ag"><h1 class="svelte-1uha8ag">strm2stl <span class="version svelte-1uha8ag">v2</span></h1> <span class="subject svelte-1uha8ag">${escape_html(app.region?.name ?? "No region selected")}</span></header> <div class="body svelte-1uha8ag"><aside class="left svelte-1uha8ag">`);
    RegionList($$renderer2);
    $$renderer2.push(`<!----></aside> <main class="centre svelte-1uha8ag"><nav class="tabs svelte-1uha8ag"><!--[-->`);
    const each_array = ensure_array_like(TABS);
    for (let $$index = 0, $$length = each_array.length; $$index < $$length; $$index++) {
      let entry = each_array[$$index];
      $$renderer2.push(`<button type="button"${attr_class("svelte-1uha8ag", void 0, { "active": tab === entry.id })}>${escape_html(entry.label)}</button>`);
    }
    $$renderer2.push(`<!--]--></nav> <div class="viewport svelte-1uha8ag">`);
    {
      $$renderer2.push("<!--[0-->");
      MapView($$renderer2);
    }
    $$renderer2.push(`<!--]--></div></main> <aside class="right svelte-1uha8ag">`);
    SettingsPanel($$renderer2);
    $$renderer2.push(`<!----></aside></div> `);
    ExportBar($$renderer2);
    $$renderer2.push(`<!----> `);
    Notices($$renderer2);
    $$renderer2.push(`<!----></div>`);
  });
}
export {
  _page as default
};
