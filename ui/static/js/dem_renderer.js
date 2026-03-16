// dem_renderer.js
// Provides functions to render DEM canvas, draw colorbar, and render histogram using Plotly

// Map elevation normalized value t in [0,1] to RGB using common colormaps
function mapElevationToColor(t, colormap) {
  // ensure t in [0,1]
  t = Math.max(0, Math.min(1, t));
  if (colormap === 'terrain') {
    // simple terrain-like gradient using HSL
    const h = 0.35 - 0.35 * t; // green->brown
    const s = 0.6;
    const l = 0.4 + 0.35 * (1 - t);
    return hslToRgb(h, s, l);
  }
  if (colormap === 'viridis') {
    // approximate viridis via bezier-ish mapping
    return [0.267 + 0.5 * t, 0.004 + 0.9 * t * (1 - t), 0.329 + 0.6 * (1 - t)];
  }
  if (colormap === 'jet') {
    // rough jet gradient
    return [Math.max(0, Math.min(1, 1.5 - Math.abs(4 * t - 3))), Math.max(0, Math.min(1, 1.5 - Math.abs(4 * t - 2))), Math.max(0, Math.min(1, 1.5 - Math.abs(4 * t - 1)))];
  }
  if (colormap === 'hot') {
    return [Math.min(1, 3 * t), Math.min(1, 3 * (t - 1 / 3)), Math.min(1, 3 * (t - 2 / 3))];
  }
  // default grayscale
  return [t, t, t];
}

function hslToRgb(h, s, l) {
  let r, g, b;
  if (s === 0) {
    r = g = b = l;
  } else {
    const hue2rgb = (p, q, t) => {
      if (t < 0) t += 1;
      if (t > 1) t -= 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    };
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, h + 1 / 3);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1 / 3);
  }
  return [r, g, b];
}

export function drawColorbar(min, max, colormap, containerId = 'colorbar') {
  const bar = document.getElementById(containerId);
  if (!bar) return;
  bar.innerHTML = '';
  const canvas = document.createElement('canvas');
  canvas.width = 300;
  canvas.height = 40;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(canvas.width, canvas.height);

  for (let x = 0; x < canvas.width; x++) {
    const t = x / (canvas.width - 1);
    const [r, g, b] = mapElevationToColor(t, colormap);
    for (let y = 0; y < canvas.height; y++) {
      const idx = (y * canvas.width + x) * 4;
      img.data[idx + 0] = Math.round(r * 255);
      img.data[idx + 1] = Math.round(g * 255);
      img.data[idx + 2] = Math.round(b * 255);
      img.data[idx + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);

  // ticks
  const numTicks = 5;
  ctx.fillStyle = '#000';
  ctx.font = '12px sans-serif';
  for (let i = 0; i <= numTicks; i++) {
    const x = Math.round((i / numTicks) * (canvas.width - 1));
    const value = min + (i / numTicks) * (max - min);
    ctx.fillRect(x, canvas.height - 10, 1, 10);
    ctx.fillText(value.toFixed(1), x - 18, canvas.height + 20);
  }

  bar.appendChild(canvas);
}

export function renderDEMCanvas(values, width, height, colormap, vmin, vmax, containerId = 'demImage') {
  const container = document.getElementById(containerId);
  if (!container) return null;
  container.innerHTML = '';
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  canvas.style.maxWidth = '100%';
  canvas.style.height = 'auto';
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(width, height);

  const range = (vmax - vmin) || 1;
  for (let i = 0; i < width * height; i++) {
    const val = Number.isFinite(values[i]) ? values[i] : NaN;
    const t = Number.isFinite(val) ? ((val - vmin) / range) : 0;
    const [r, g, b] = mapElevationToColor(t, colormap);
    const idx = i * 4;
    img.data[idx] = Math.round(r * 255);
    img.data[idx + 1] = Math.round(g * 255);
    img.data[idx + 2] = Math.round(b * 255);
    img.data[idx + 3] = Number.isFinite(val) ? 255 : 0;
  }
  ctx.putImageData(img, 0, 0);
  container.appendChild(canvas);
  return canvas;
}

export function renderHistogram(values, colormap, containerId = 'histogram') {
  const container = document.getElementById(containerId);
  if (!container) return;
  // Use Plotly for an interactive histogram
  const trace = {
    x: values.filter(v => Number.isFinite(v)),
    type: 'histogram',
    marker: { color: 'rgba(100,150,255,0.7)', line: { color: 'black', width: 1 } }
  };
  const layout = { title: 'Elevation Histogram', bargap: 0.05, margin: { t: 30, b: 30 } };
  Plotly.newPlot(container, [trace], layout, { responsive: true });
}
