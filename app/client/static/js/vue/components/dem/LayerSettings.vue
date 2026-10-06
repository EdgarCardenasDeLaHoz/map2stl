<template>
  <!-- Edit page, right: the selected layer's settings (F-EDITPANEL, mockup
       claude/mockups/2026-10-04-edit-panel/panel.png): the selected layer with tabs View / Fetch /
       Composite (user 2026-10-05). Canvas (the whole map and print) is a layer row of its own
       (mockup claude/mockups/2026-10-05-canvas-row). Rows: settings/layerGroups.ts.
       A sub-page (›) shows one of the old sections, moved here by its CollapsibleSection. -->
  <section id="layerProperties" class="ls" :aria-label="`${layer.name} settings`">
    <div v-show="panel.sub" class="ls-sub">
      <button type="button" class="ls-back" @click="panel.close()">‹ {{ layer.name }}</button>
      <h2 class="ls-title">{{ panel.subTitle }}</h2>
      <div id="lsSubBody" class="ls-sub-body"></div>
    </div>

    <div v-show="!panel.sub">
      <h2 class="ls-title">{{ layer.name }}</h2>
      <div class="ls-subt">{{ subtitle }}</div>

      <div class="ls-tabs" role="tablist" :aria-label="`${layer.name} settings`">
        <button v-for="g in groups" :key="g.tab" type="button" role="tab" class="ls-tab"
                :class="{ on: g.tab === tab }" :aria-selected="g.tab === tab" :disabled="!hasContent(g)"
                :title="hasContent(g) ? '' : `Nothing to ${g.tab} for ${layer.name.toLowerCase()}`"
                @click="panel.setTab(g.tab)">{{ g.title }}</button>
      </div>

      <template v-if="active">
        <div class="ls-cap ls-tabcap">
          <span></span>
          <button type="button" class="ls-link" @click="reset(active.rows)">Reset</button>
        </div>
        <div v-if="visible(active.rows, active.key).length" class="ls-grp">
          <SetRow v-for="r in visible(active.rows, active.key)" :key="r.label" :row="r" />
        </div>
        <template v-if="active.tab === 'fetch'">
          <div v-if="statusText" class="ls-status">{{ statusText }}</div>
          <button v-if="def.reload" type="button" class="ls-btn" :class="{ pri: def.reload.primary }"
                  @click="click(def.reload.click)">{{ def.reload.label }}</button>
          <div v-if="def.links?.length" class="ls-links">
            <button v-for="l in def.links" :key="l.label" type="button" class="ls-link" @click="click(l.click)">{{ l.label }}</button>
          </div>
        </template>
        <div v-if="active.key === noteKey && def.note" class="ls-note">{{ def.note }}</div>
        <button v-if="active.rows.some((r) => r.adv)" type="button" class="ls-link ls-adv" @click="panel.toggleAdvanced(active.key)">
          {{ panel.advanced[active.key] ? 'Hide advanced' : 'Show advanced' }}
        </button>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, watch } from 'vue';
import { EDIT_LAYERS, useEditLayersStore } from '../../stores/editLayers';
import { useEditPanelStore, type PanelTab } from '../../stores/editPanel';
import { setChecked, setField, val } from '../../dom-fields';
import SetRow from './settings/SetRow.vue';
import { GROUPS, RES_IDS, click, type Row } from './settings/layerGroups';

const store = useEditLayersStore();
const panel = useEditPanelStore();
const layer = computed(() => EDIT_LAYERS.find((l) => l.id === store.selected) || EDIT_LAYERS[0]);
const def = computed(() => GROUPS[layer.value.id] || GROUPS.terrain);

const SUBTITLES: Record<string, string> = {
  canvas: 'The whole map and print: every layer',
  terrain: 'The ground every other layer sits on',
  water: 'Carved into the model, as printed',
  city: 'Raised from the terrain in the 3D model',
  satellite: 'Colour only: no change to the shape',
  trails: 'Ski pistes and hiking paths',
  landcover: 'Forest, fields, built-up areas',
  mesh: 'A model you imported, placed on the terrain',
  borders: 'Country and state lines on the map; not printed',
};
const subtitle = computed(() => SUBTITLES[layer.value.id] || '');

/** The layer's own status line from its loader (the old Fetch section's status element). */
const STATUS: Record<string, string> = {
  water: 'waterHydrologyStatus', city: 'cityDataStatus', satellite: 'satImgStatus', trails: 'trailsStatus',
};
const statusText = computed(() => {
  void store.tick;
  const id = STATUS[layer.value.id];
  return id ? (document.getElementById(id)?.textContent || '').trim() : '';
});

const groups = computed(() => {
  const id = layer.value.id;
  return [
    { tab: 'view' as PanelTab, key: `${id}:view`, title: 'View', rows: def.value.view },
    { tab: 'fetch' as PanelTab, key: `${id}:fetch`, title: 'Fetch', rows: def.value.fetch },
    { tab: 'composite' as PanelTab, key: `${id}:composite`, title: 'Composite', rows: def.value.composite },
  ];
});
type Group = (typeof groups.value)[number];

// A tab with no rows and no action is disabled (Borders: nothing to fetch or print).
function hasContent(g: Group): boolean {
  return !!g.rows.length || (g.tab === 'fetch' && !!(def.value.reload || def.value.links?.length));
}
/** The chosen tab, or the first one with something in it when this layer has nothing there. */
const tab = computed<PanelTab>(() => {
  const want = groups.value.find((g) => g.tab === panel.tab);
  return want && hasContent(want) ? want.tab : (groups.value.find(hasContent)?.tab ?? panel.tab);
});
const active = computed(() => groups.value.find((g) => g.tab === tab.value && hasContent(g)) || null);
// The layer's note goes under Composite, or under View when it has no Composite rows.
const noteKey = computed(() => {
  const id = layer.value.id;
  return def.value.composite.length ? `${id}:composite` : `${id}:view`;
});

function visible(rows: Row[], key: string): Row[] {
  void store.tick;
  return rows.filter((r) => (!r.adv || panel.advanced[key]) && (!r.show || r.show()));
}

/** Per-group Reset: each control back to the value it had in the page's markup. */
function reset(rows: Row[]) {
  for (const r of rows) {
    if (!r.id || r.get) continue;
    const e = document.getElementById(r.id) as HTMLInputElement | HTMLSelectElement | null;
    if (!e) continue;
    if (r.kind === 'res') setField(r.id, val('paramDim') || '600');
    else if (e instanceof HTMLInputElement && e.type === 'checkbox') setChecked(r.id, e.defaultChecked);
    else if (e instanceof HTMLSelectElement) {
      const d = [...e.options].find((o) => o.defaultSelected) || e.options[0];
      if (d) setField(r.id, d.value);
    } else setField(r.id, (e as HTMLInputElement).defaultValue);
  }
  store.bump();
}

// ── Rules that keep one setting one setting ──────────────────────────────────
let lastDim = '';
/** A layer resolution equal to the old Detail follows it (user 2026-10-04: Detail, or "Own resolution"). */
function onChange(e: Event) {
  const id = (e.target as HTMLElement | null)?.id;
  if (id === 'paramDim') {
    const dim = val('paramDim') || '';
    for (const r of RES_IDS) if (lastDim && val(r) === lastDim) setField(r, dim);
    lastDim = dim;
    return;
  }
  // The print's city switches and building height drive the 2D map preview's channels,
  // which used to be separate controls under Composite > City / OSM.
  const SYNC: Record<string, string> = {
    cityLayer_buildings_enabled: 'compositeBuildingsEnabled', cityLayer_roads_enabled: 'compositeRoadsEnabled',
    cityLayer_waterways_enabled: 'compositeWaterwaysEnabled', cityLayer_walls_enabled: 'compositeWallsEnabled',
  };
  if (id && SYNC[id]) setChecked(SYNC[id], (e.target as HTMLInputElement).checked);
  if (id === 'cityLayer_buildings_value') setField('compositeBuildingScale', val(id) || '1');
}
// A sub-page's canvases (height curve, histogram) measure themselves on resize.
watch(() => panel.sub, () => nextTick(() => window.dispatchEvent(new Event('resize'))));
watch(() => store.selected, () => panel.close());

onMounted(() => {
  lastDim = val('paramDim') || '';
  document.addEventListener('change', onChange, true);
});
onBeforeUnmount(() => document.removeEventListener('change', onChange, true));
</script>

<style scoped>
.ls { background: #1c1c1e; border-radius: 18px; padding: 16px 14px 14px; margin-bottom: 12px; color: #f5f5f7; }
.ls-title { margin: 0; font-size: 20px; font-weight: 700; }
.ls-subt { color: #a1a1a6; font-size: 13px; margin: 2px 0 6px; }
.ls-cap { display: flex; align-items: center; justify-content: space-between; margin: 16px 4px 6px;
  font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: #a1a1a6; }
.ls-grp { background: #2c2c2e; border-radius: 12px; overflow: hidden; }
.ls-link { background: none; border: 0; padding: 0; color: #f5f5f7; text-decoration: underline; font-size: 12px; cursor: pointer;
  text-transform: none; letter-spacing: 0; font-weight: 400; }
.ls-adv { display: block; margin: 8px 4px 0; color: #a1a1a6; }
.ls-btn { display: block; width: 100%; margin-top: 8px; padding: 8px 0; border: 0; border-radius: 10px; cursor: pointer;
  background: #2c2c2e; color: #f5f5f7; font-size: 13px; font-weight: 600; }
.ls-btn:hover { background: #3a3a3c; }
.ls-btn.pri { background: #0a84ff; }
.ls-btn.pri:hover { background: #2f95ff; }
.ls-links { display: flex; gap: 12px; flex-wrap: wrap; margin: 8px 4px 0; }
.ls-links .ls-link { color: #a1a1a6; }
.ls-status { color: #a1a1a6; font-size: 12px; margin: 8px 4px 0; }
.ls-note { color: #a1a1a6; font-size: 11.5px; margin: 6px 4px 0; }
.ls-back { background: none; border: 0; color: #f5f5f7; text-decoration: underline; font-size: 13px; padding: 0; cursor: pointer; margin-bottom: 8px; }
.ls-sub-body { margin-top: 10px; }
.ls-tabs { display: flex; background: #2c2c2e; border-radius: 9px; padding: 2px; margin: 10px 0 0; }
.ls-tab { flex: 1; border: 0; background: none; color: #f5f5f7; padding: 6px 4px; border-radius: 7px;
  font-size: 13px; cursor: pointer; }
.ls-tab.on { background: #636366; font-weight: 600; }
.ls-tab:disabled { color: #636366; cursor: default; }
.ls-tab:focus-visible { outline: 2px solid #0a84ff; outline-offset: 1px; }
.ls-tabcap { margin-top: 8px; }
.ls-sub-body :deep(.collapsible-section) { margin: 0 0 12px; background: #2c2c2e; border-radius: 12px; padding: 10px; }
</style>
