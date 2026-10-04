<template>
  <!-- One settings row (F-EDITPANEL): icon, name, one plain line, the control on the right or
       below. It writes the existing control by id (layerGroups.ts). -->
  <div class="sr" :class="{ stack: stacked }">
    <div class="sr-head">
      <span class="sr-ic" :style="{ background: row.color }" aria-hidden="true">{{ row.icon }}</span>
      <div class="sr-t">
        <b>{{ row.label }}</b>
        <span v-if="hintText">{{ hintText }}</span>
      </div>

      <input v-if="row.kind === 'switch'" type="checkbox" role="switch" class="sr-switch" :aria-label="row.label"
             :checked="!!value" @change="write(($event.target as HTMLInputElement).checked)">
      <span v-else-if="row.kind === 'slider'" class="sr-val">{{ row.fmt ? row.fmt(Number(value)) : value }}</span>
      <template v-else-if="row.kind === 'number'">
        <input type="number" class="sr-num" :aria-label="row.label" :value="value"
               :min="row.min" :max="row.max" :step="row.step"
               @change="write(($event.target as HTMLInputElement).value)">
        <span v-if="row.unit" class="sr-unit">{{ row.unit }}</span>
      </template>
      <input v-else-if="row.kind === 'color'" type="color" class="sr-color" :aria-label="row.label" :value="value"
             @input="write(($event.target as HTMLInputElement).value)">
      <button v-else-if="row.kind === 'sub'" type="button" class="sr-go" :aria-label="`Open ${row.label}`"
              @click="panel.open(row.sub!, SUB_TITLES[row.sub!] || row.label)">›</button>
      <button v-else-if="row.kind === 'button'" type="button" class="sr-go" :aria-label="row.label"
              @click="click(row.click!)">›</button>
    </div>

    <input v-if="row.kind === 'slider'" type="range" class="sr-range" :aria-label="row.label"
           :min="row.min" :max="row.max" :step="row.step" :value="draft ?? value"
           @input="draft = ($event.target as HTMLInputElement).value"
           @change="draft = null; write(($event.target as HTMLInputElement).value)">

    <select v-else-if="row.kind === 'select'" class="sr-select" :aria-label="row.label" :value="value"
            @change="write(($event.target as HTMLSelectElement).value)">
      <option v-for="o in opts" :key="o.value" :value="o.value" :disabled="o.disabled">{{ o.label }}</option>
    </select>

    <div v-else-if="row.kind === 'seg'" class="sr-seg" role="radiogroup" :aria-label="row.label">
      <button v-for="o in row.options" :key="o.value" type="button" role="radio"
              :aria-checked="value === o.value" :class="{ on: value === o.value }"
              @click="write(o.value)">{{ o.label }}</button>
    </div>

    <div v-else-if="row.kind === 'res'" class="sr-res">
      <label><input type="checkbox" class="sr-ck" :checked="own" @change="setOwn(($event.target as HTMLInputElement).checked)">
        Own resolution</label>
      <span class="grow"></span>
      <input type="number" class="sr-num" :disabled="!own" aria-label="Own resolution in pixels"
             min="50" max="4000" step="50" :value="value" @change="write(($event.target as HTMLInputElement).value)">
      <span class="sr-unit">px</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { checked, options, setChecked, setField, val } from '../../../dom-fields';
import { useEditLayersStore } from '../../../stores/editLayers';
import { useEditPanelStore } from '../../../stores/editPanel';
import { SUB_TITLES, click, type Row } from './layerGroups';

const props = defineProps<{ row: Row }>();
const store = useEditLayersStore();
const panel = useEditPanelStore();
const draft = ref<string | null>(null);

const stacked = computed(() => ['slider', 'seg', 'res', 'select'].includes(props.row.kind));

const value = computed(() => {
  void store.tick;
  const r = props.row;
  if (r.get) return r.get();
  if (!r.id) return '';
  return r.kind === 'switch' ? checked(r.id) : (val(r.id) ?? '');
});

const hintText = computed(() => {
  void store.tick;
  const h = props.row.hint;
  if (props.row.kind === 'res') {
    const dim = val('paramDim') ?? '';
    return own.value ? `Own grid for the ${String(h || '').replace(/^Grid of the /, '')}` : `Same as the terrain (${dim} px)`;
  }
  return typeof h === 'function' ? h() : h || '';
});

const opts = computed(() => {
  void store.tick;
  return props.row.options || (props.row.id ? options(props.row.id) : []);
});

/** A resolution is the layer's own when it differs from the terrain's Detail. */
const own = computed(() => {
  void store.tick;
  return props.row.kind === 'res' && !!props.row.id && val(props.row.id) !== val('paramDim');
});

function write(v: string | boolean) {
  const r = props.row;
  if (r.set) r.set(v);
  else if (r.id && r.kind === 'switch') setChecked(r.id, !!v);
  else if (r.id) setField(r.id, String(v));
  if (r.click && r.kind !== 'button') click(r.click);
  store.bump();
}

function setOwn(on: boolean) {
  const id = props.row.id!;
  const dim = Number(val('paramDim') || 600);
  // Ticked: start from a different grid so it reads as the layer's own; unticked: follow Detail.
  setField(id, String(on ? (dim >= 1000 ? dim / 2 : dim * 2) : dim));
  store.bump();
}
</script>

<style scoped>
.sr { display: flex; flex-direction: column; gap: 7px; padding: 9px 12px; border-top: 1px solid #3a3a3c; min-height: 44px; justify-content: center; }
.sr:first-child { border-top: 0; }
.sr-head { display: flex; align-items: center; gap: 10px; }
.sr-ic { width: 26px; height: 26px; border-radius: 7px; display: grid; place-items: center; font-size: 13px; flex: none; color: #fff; }
.sr-t { flex: 1; min-width: 0; }
.sr-t b { display: block; font-size: 13px; font-weight: 600; color: #f5f5f7; }
.sr-t span { display: block; font-size: 11.5px; color: #a1a1a6; line-height: 1.3; }
.sr-val, .sr-unit { color: #a1a1a6; font-size: 12.5px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.sr-num { width: 62px; flex: none; background: #1c1c1e; color: #f5f5f7; border: 1px solid #48484a; border-radius: 8px; padding: 4px 8px; text-align: right; font-size: 13px; }
.sr-num:disabled { color: #6e6e73; border-color: #3a3a3c; }
.sr-select { margin-left: 36px; width: calc(100% - 36px); background: #1c1c1e; color: #f5f5f7; border: 1px solid #48484a; border-radius: 8px; padding: 4px 6px; font-size: 12.5px; }
.sr-color { width: 36px; height: 24px; border: 0; background: none; padding: 0; cursor: pointer; }
.sr-go { background: none; border: 0; color: #a1a1a6; font-size: 20px; line-height: 1; cursor: pointer; padding: 0 4px; }
.sr-go:hover { color: #f5f5f7; }
.sr-range { margin: 0 2px 0 36px; width: calc(100% - 38px); accent-color: #0a84ff; }
.sr-seg { display: flex; margin-left: 0; background: #1c1c1e; border-radius: 10px; padding: 3px; gap: 2px; }
.sr-seg button { flex: 1 1 auto; min-width: 0; border: 0; border-radius: 8px; padding: 5px 2px; white-space: nowrap; background: none; color: #a1a1a6; font-size: 12px; cursor: pointer; }
.sr-seg button:hover { color: #f5f5f7; }
.sr-seg button.on { background: #3a3a3c; color: #f5f5f7; font-weight: 600; }
.sr-res { display: flex; align-items: center; gap: 6px; margin-left: 0; white-space: nowrap; font-size: 12.5px; color: #a1a1a6; }
.sr-res label { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.sr-res .grow { flex: 1; }
.sr-ck { width: 16px; height: 16px; accent-color: #0a84ff; margin: 0; }
.sr-switch {
  appearance: none; -webkit-appearance: none; flex: none; position: relative; cursor: pointer;
  width: 40px; height: 24px; border-radius: 999px; background: #48484a; margin: 0; transition: background .15s;
}
.sr-switch::after { content: ""; position: absolute; top: 2px; left: 2px; width: 20px; height: 20px; border-radius: 50%; background: #fff; transition: left .15s; }
.sr-switch:checked { background: #30d158; }
.sr-switch:checked::after { left: 18px; }
.sr-switch:focus-visible, .sr-go:focus-visible, .sr-seg button:focus-visible { outline: 2px solid #0a84ff; outline-offset: 2px; }
</style>
