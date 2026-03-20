/* DEAD — not loaded by app.js or index.html. Editing this file has no effect on the running app. */
/**
 * 3D Maps Application - Main Entry Point
 * Initializes all components and sets up event handlers
 */

import { getState, updateState, updateCache, getCurrentBounds, hasSelection } from './state.js';
import { loadCoordinates, saveCoordinate, submitBoundingBox } from './api.js';
import { initMap, drawRegionRectangles, zoomToBounds, addRegionRectangle, clearAllBoundingBoxes } from './components/map.js';
import { loadDEM, recolorDEM, loadWaterMask, renderCombinedView, setupGridToggle, applyWaterSubtract, drawGridlinesOverlay } from './components/dem-viewer.js';

/**
 * Initialize the application
 */
export async function initApp() {
  console.log('Initializing 3D Maps application...');

  try {
    // Initialize map
    initMapComponent();
    console.log('Map initialized');

    // Initialize globe (Three.js)
    initGlobe();
    console.log('Globe initialized');

    // Load saved coordinates
    await loadCoordinatesData();
    console.log('Coordinates loaded');

    // Setup event listeners
    setupEventListeners();
    setupDemSubtabs();
    setupWaterMaskListeners();
    setupGridToggle();

    console.log('App initialization complete');
  } catch (error) {
    console.error('Error initializing app:', error);
  }
}

/**
 * Initialize map component
 */
function initMapComponent() {
  // Check if Leaflet is loaded
  if (typeof L === 'undefined') {
    console.error('Leaflet library not loaded!');
    document.getElementById('coordinatesList').innerHTML =
      '<div class="loading" style="color:red">Error: Leaflet library failed to load.</div>';
    return;
  }

  initMap('map');
}

/**
 * Initialize Three.js globe
 */
function initGlobe() {
  try {
    const container = document.getElementById('globe');
    if (!container || container.clientWidth === 0 || container.clientHeight === 0) {
      console.warn('Globe container not ready, skipping init');
      return;
    }

    const state = getState();

    state.globe.scene = new THREE.Scene();
    state.globe.camera = new THREE.PerspectiveCamera(
      75,
      container.clientWidth / container.clientHeight,
      0.1,
      1000
    );
    state.globe.renderer = new THREE.WebGLRenderer({ antialias: true });
    state.globe.renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(state.globe.renderer.domElement);

    // Create globe geometry
    const geometry = new THREE.SphereGeometry(5, 64, 64);
    const material = new THREE.MeshPhongMaterial({
      color: 0x2233ff,
      transparent: true,
      opacity: 0.8
    });
    state.globe.mesh = new THREE.Mesh(geometry, material);
    state.globe.scene.add(state.globe.mesh);

    // Add lighting
    const ambientLight = new THREE.AmbientLight(0x404040);
    state.globe.scene.add(ambientLight);

    const directionalLight = new THREE.DirectionalLight(0xffffff, 0.5);
    directionalLight.position.set(1, 1, 1);
    state.globe.scene.add(directionalLight);

    state.globe.camera.position.z = 10;

    // Add markers group
    state.globe.scene.add(new THREE.Group());

    // Start animation
    animateGlobe();
  } catch (error) {
    console.error('Error initializing globe:', error);
  }
}

/**
 * Animate globe rotation
 */
function animateGlobe() {
  const state = getState();

  requestAnimationFrame(animateGlobe);

  if (state.globe.mesh) {
    state.globe.mesh.rotation.y += 0.005;
  }

  if (state.globe.renderer && state.globe.scene && state.globe.camera) {
    state.globe.renderer.render(state.globe.scene, state.globe.camera);
  }
}

/**
 * Load coordinates from API
 */
async function loadCoordinatesData() {
  const list = document.getElementById('coordinatesList');
  const select = document.getElementById('regionSelect');

  if (!list || !select) return;

  list.innerHTML = '<div class="loading">Loading coordinates...</div>';

  try {
    const regions = await loadCoordinates();
    updateState({ coordinates: regions });

    // Populate select dropdown
    select.innerHTML = '<option value="">-- Choose a region --</option>';
    regions.forEach(region => {
      const option = document.createElement('option');
      option.value = region.name;
      option.textContent = `${region.name} - ${region.description}`;
      select.appendChild(option);
    });

    // Populate list
    list.innerHTML = '';

    if (regions.length === 0) {
      list.innerHTML = '<div class="loading">No regions found</div>';
      return;
    }

    regions.forEach((region, index) => {
      const item = document.createElement('div');
      item.className = 'coordinate-item';
      item.textContent = `${region.name}: N${region.north}° S${region.south}° E${region.east}° W${region.west}°`;
      item.onclick = () => selectCoordinate(index);
      list.appendChild(item);
    });

    // Draw rectangles on map
    drawRegionRectangles(regions);

    // Update globe markers
    updateGlobeMarkers(regions);

  } catch (error) {
    console.error('Error loading coordinates:', error);
    list.innerHTML = `<div class="loading" style="color:red;">Error: ${error.message}</div>`;
  }
}

/**
 * Update markers on the globe
 */
function updateGlobeMarkers(regions) {
  const state = getState();

  if (!state.globe.scene || state.globe.scene.children.length < 3) return;

  const markersGroup = state.globe.scene.children[2];
  if (!markersGroup || !markersGroup.children) return;

  // Clear existing markers
  while (markersGroup.children.length > 0) {
    markersGroup.remove(markersGroup.children[0]);
  }

  // Add markers for each region
  regions.forEach(region => {
    const centerLat = (region.north + region.south) / 2;
    const centerLng = (region.east + region.west) / 2;

    const phi = (90 - centerLat) * (Math.PI / 180);
    const theta = (centerLng + 180) * (Math.PI / 180);

    const geometry = new THREE.SphereGeometry(0.05, 8, 8);
    const material = new THREE.MeshBasicMaterial({ color: 0xff0000 });
    const marker = new THREE.Mesh(geometry, material);

    marker.position.x = 5 * Math.sin(phi) * Math.cos(theta);
    marker.position.y = 5 * Math.cos(phi);
    marker.position.z = 5 * Math.sin(phi) * Math.sin(theta);

    markersGroup.add(marker);
  });
}

/**
 * Select a coordinate from the list
 */
function selectCoordinate(index) {
  const state = getState();
  const region = state.coordinates[index];

  if (!region) return;

  updateState({ selectedRegion: region });

  // Update UI
  document.querySelectorAll('.coordinate-item').forEach((item, i) => {
    item.classList.toggle('selected', i === index);
  });

  // Load parameters if available
  if (region.parameters) {
    document.getElementById('paramDim').value = region.parameters.dim || 200;
    document.getElementById('paramDepthScale').value = region.parameters.depth_scale || 0.5;
    document.getElementById('paramWaterScale').value = region.parameters.water_scale || 0.05;
    document.getElementById('paramHeight').value = region.parameters.height || 10;
    document.getElementById('paramBase').value = region.parameters.base || 2;
    document.getElementById('paramSubtractWater').checked = region.parameters.subtract_water !== false;
    document.getElementById('paramSatScale').value = region.parameters.sat_scale || 500;
  }

  // Update map view
  zoomToBounds(region);
  addRegionRectangle(region);

  // Switch to DEM tab and load both DEM and water mask
  loadAllLayers();
}

/**
 * Load DEM for current selection
 */
async function loadDEMForSelection(highRes = false) {
  const bounds = getCurrentBounds();

  if (!bounds) {
    document.getElementById('demImage').innerHTML = '<p>Please select a region first.</p>';
    return;
  }

  const params = {
    dim: highRes ? 400 : parseInt(document.getElementById('paramDim').value) || 200,
    depth_scale: parseFloat(document.getElementById('paramDepthScale').value) || 0.5,
    water_scale: parseFloat(document.getElementById('paramWaterScale').value) || 0.05,
    height: parseInt(document.getElementById('paramHeight').value) || 10,
    base: parseInt(document.getElementById('paramBase').value) || 2,
    subtract_water: document.getElementById('paramSubtractWater').checked,
    dataset: document.getElementById('demDataset')?.value || 'esa',
    show_landuse: document.getElementById('paramLandUse')?.checked || false,
    colormap: document.getElementById('demColormap')?.value || 'terrain'
  };

  await loadDEM(bounds, params);
}

// Expose to window for backward compatibility
window.loadDEM = loadDEMForSelection;

/**
 * Setup event listeners
 */
function setupEventListeners() {
  // Tab buttons
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => switchView(tab.dataset.view));
  });

  // Control buttons
  document.getElementById('loadRegionBtn')?.addEventListener('click', loadSelectedRegion);
  document.getElementById('saveRegionBtn')?.addEventListener('click', saveCurrentRegion);
  document.getElementById('submitBtn')?.addEventListener('click', submitBbox);
  document.getElementById('updateDemBtn')?.addEventListener('click', () => loadDEMForSelection());
  document.getElementById('generateModelBtn')?.addEventListener('click', generateModel);
  document.getElementById('toggleCoords')?.addEventListener('click', toggleCoordsTable);
  document.getElementById('toggleSidebar')?.addEventListener('click', toggleSidebar);
  document.getElementById('loadAllLayersBtn')?.addEventListener('click', loadAllLayers);
  document.getElementById('clearBboxBtn')?.addEventListener('click', clearAllBoundingBoxes);

  // Model tab buttons
  document.getElementById('generateModelBtn2')?.addEventListener('click', generateModel);
  document.getElementById('downloadSTLBtn')?.addEventListener('click', downloadSTL);
  document.getElementById('previewModelBtn')?.addEventListener('click', previewModel);

  // Colormap change
  document.getElementById('demColormap')?.addEventListener('change', (e) => {
    recolorDEM(e.target.value);
  });

  // Region select
  document.getElementById('regionSelect')?.addEventListener('change', (e) => {
    const regionName = e.target.value;
    if (regionName) {
      const state = getState();
      const region = state.coordinates.find(r => r.name === regionName);
      if (region) {
        selectCoordinate(state.coordinates.indexOf(region));
      }
    }
  });

  // Sidebar open button
  document.getElementById('openSidebarBtn')?.addEventListener('click', () => {
    const sidebar = document.getElementById('sidebar');
    const toggleBtn = document.getElementById('toggleSidebar');
    sidebar.classList.remove('collapsed');
    toggleBtn.textContent = '◀';
    document.getElementById('openSidebarBtn').classList.add('hidden');
  });
}

/**
 * Switch between views (map, globe, dem, model)
 */
function switchView(view) {
  const containers = {
    map: document.getElementById('mapContainer'),
    globe: document.getElementById('globeContainer'),
    dem: document.getElementById('demContainer'),
    model: document.getElementById('modelContainer')
  };

  // Hide all containers
  Object.values(containers).forEach(c => {
    if (c) {
      c.classList.add('hidden');
      if (c.id === 'modelContainer') {
        c.style.display = 'none';
      }
    }
  });

  // Remove active from tabs
  document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));

  // Show selected container
  const container = containers[view];
  if (container) {
    container.classList.remove('hidden');
    if (view === 'model') {
      container.style.display = 'flex';
    }
  }

  // Activate tab
  document.querySelector(`[data-view="${view}"]`)?.classList.add('active');
}

/**
 * Load selected region
 */
function loadSelectedRegion() {
  const state = getState();

  if (!state.selectedRegion) {
    alert('Please select a region first!');
    return;
  }

  alert(`Region "${state.selectedRegion.name}" loaded!`);
}

/**
 * Save current region
 */
async function saveCurrentRegion() {
  const state = getState();

  if (!state.boundingBox) {
    alert('Please draw a bounding box first!');
    return;
  }

  const regionName = document.getElementById('regionName').value.trim();
  if (!regionName) {
    alert('Please enter a name for the region!');
    return;
  }

  const bounds = state.boundingBox;
  const regionData = {
    name: regionName,
    north: bounds.getNorth(),
    south: bounds.getSouth(),
    east: bounds.getEast(),
    west: bounds.getWest(),
    description: `Custom region: ${regionName}`,
    parameters: {
      dim: parseInt(document.getElementById('paramDim').value) || 200,
      depth_scale: parseFloat(document.getElementById('paramDepthScale').value) || 0.5,
      water_scale: parseFloat(document.getElementById('paramWaterScale').value) || 0.05,
      height: parseInt(document.getElementById('paramHeight').value) || 10,
      base: parseInt(document.getElementById('paramBase').value) || 2,
      subtract_water: document.getElementById('paramSubtractWater').checked
    }
  };

  try {
    await saveCoordinate(regionData);
    alert('Region saved successfully!');
    await loadCoordinatesData();
    document.getElementById('regionName').value = '';
  } catch (error) {
    alert('Error saving region: ' + error.message);
  }
}

/**
 * Submit bounding box
 */
async function submitBbox() {
  const bounds = getCurrentBounds();

  if (!bounds) {
    alert('Please draw a bounding box first!');
    return;
  }

  try {
    await submitBoundingBox(bounds);
    alert('Bounding box submitted successfully!');
  } catch (error) {
    alert('Failed to submit bounding box: ' + error.message);
  }
}

/**
 * Load all layers (DEM, water mask, land cover)
 */
async function loadAllLayers() {
  if (!hasSelection()) {
    alert('Please select a region or draw a bounding box first.');
    return;
  }

  switchView('dem');
  document.getElementById('demImage').innerHTML = '<p style="text-align:center;padding:50px;">Loading all layers...</p>';

  try {
    const bounds = getCurrentBounds();

    await loadDEMForSelection();
    await loadWaterMask(bounds, {
      sat_scale: parseInt(document.getElementById('paramSatScale').value) || 500,
      dim: parseInt(document.getElementById('paramDim').value) || 200
    });

    switchDemSubtab('combined');
    renderCombinedView();

  } catch (error) {
    console.error('Error loading layers:', error);
    alert('Error loading layers: ' + error.message);
  }
}

/**
 * Toggle sidebar
 */
function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const toggleBtn = document.getElementById('toggleSidebar');
  const openBtn = document.getElementById('openSidebarBtn');

  if (sidebar.classList.contains('collapsed')) {
    sidebar.classList.remove('collapsed');
    toggleBtn.textContent = '◀';
    openBtn.classList.add('hidden');
  } else {
    sidebar.classList.add('collapsed');
    toggleBtn.textContent = '▶';
    openBtn.classList.remove('hidden');
  }
}

/**
 * Toggle coordinates table
 */
function toggleCoordsTable() {
  const table = document.getElementById('coordsTable');
  const button = document.getElementById('toggleCoords');

  if (table.style.display === 'none') {
    table.style.display = 'block';
    button.textContent = 'Collapse';
    populateCoordsTable();
  } else {
    table.style.display = 'none';
    button.textContent = 'Expand';
  }
}

/**
 * Populate coordinates table
 */
function populateCoordsTable() {
  const tbody = document.getElementById('coordsBody');
  const state = getState();

  tbody.innerHTML = '';
  state.coordinates.forEach(region => {
    const row = document.createElement('tr');
    row.innerHTML = `
            <td>${region.name}</td>
            <td>${region.north}</td>
            <td>${region.south}</td>
            <td>${region.east}</td>
            <td>${region.west}</td>
            <td>${region.description || ''}</td>
        `;
    tbody.appendChild(row);
  });
}

/**
 * Setup DEM subtabs
 */
function setupDemSubtabs() {
  document.querySelectorAll('.dem-subtab').forEach(tab => {
    tab.addEventListener('click', () => {
      switchDemSubtab(tab.dataset.subtab);
    });
  });
}

/**
 * Switch DEM subtab
 */
function switchDemSubtab(subtab) {
  // Update active tab
  document.querySelectorAll('.dem-subtab').forEach(t => {
    t.classList.toggle('active', t.dataset.subtab === subtab);
  });

  // Hide all containers
  document.getElementById('demSubtabContent')?.classList.add('hidden');
  document.getElementById('waterMaskContainer')?.classList.add('hidden');
  document.getElementById('satelliteContainer')?.classList.add('hidden');
  document.getElementById('combinedContainer')?.classList.add('hidden');

  // Show selected
  switch (subtab) {
    case 'dem':
      document.getElementById('demSubtabContent')?.classList.remove('hidden');
      break;
    case 'water':
      document.getElementById('waterMaskContainer')?.classList.remove('hidden');
      break;
    case 'satellite':
      document.getElementById('satelliteContainer')?.classList.remove('hidden');
      break;
    case 'combined':
      document.getElementById('combinedContainer')?.classList.remove('hidden');
      break;
  }
}

/**
 * Setup water mask event listeners
 */
function setupWaterMaskListeners() {
  document.getElementById('loadWaterMaskBtn')?.addEventListener('click', async () => {
    const bounds = getCurrentBounds();
    if (!bounds) {
      alert('Please select a region first.');
      return;
    }
    await loadWaterMask(bounds, {
      sat_scale: parseInt(document.getElementById('paramSatScale').value) || 500,
      dim: parseInt(document.getElementById('paramDim').value) || 200
    });
  });

  document.getElementById('applyWaterSubtractBtn')?.addEventListener('click', applyWaterSubtract);

  document.getElementById('previewWaterSubtractBtn')?.addEventListener('click', () => {
    renderCombinedView();
  });

  // Slider value displays
  document.getElementById('waterScaleSlider')?.addEventListener('input', (e) => {
    document.getElementById('waterScaleValue').textContent = e.target.value;
  });

  document.getElementById('waterOpacity')?.addEventListener('input', (e) => {
    document.getElementById('waterOpacityValue').textContent = e.target.value;
  });

  document.getElementById('waterThreshold')?.addEventListener('input', (e) => {
    document.getElementById('waterThresholdValue').textContent = e.target.value;
  });
}

/**
 * Generate 3D model (placeholder)
 */
function generateModel() {
  alert('Model generation not yet fully implemented.');
}

/**
 * Download STL (placeholder)
 */
function downloadSTL() {
  alert('STL download not yet fully implemented.');
}

/**
 * Preview model in 3D (placeholder)
 */
function previewModel() {
  alert('3D preview not yet fully implemented.');
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}

// Export for module usage
export default {
  initApp,
  loadDEMForSelection,
  switchView,
  selectCoordinate
};