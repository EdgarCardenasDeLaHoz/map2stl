<script lang="ts">
	/**
	 * The mesh as it will be exported, in three.js.
	 *
	 * The height math here is a deliberate mirror of `to_model_space` in the server's
	 * pipeline: exaggerate, optionally cap at sea level, normalise into `modelHeight`, add
	 * the base. Keeping the two in step is what makes this a preview rather than an
	 * illustration — in v1 the 3D view and the exported file were computed by different code
	 * with different defaults, so the preview could not be trusted for anything but shape.
	 *
	 * The grid is decimated to at most PREVIEW_MAX per side. A 1600-wide DEM is 2.56 M
	 * vertices, which the exporter handles fine and a WebGL preview does not need.
	 */
	import { app } from '$lib/state.svelte';
	import { onMount, untrack } from 'svelte';

	const PREVIEW_MAX = 320;

	let container: HTMLDivElement;
	let three: any = null;
	let renderer: any = null;
	let scene: any = null;
	let camera: any = null;
	let controls: any = null;
	let mesh: any = null;
	let ready = $state(false);
	let busy = $state(false);

	onMount(() => {
		let disposed = false;
		(async () => {
			three = await import('three');
			const { OrbitControls } = await import('three/examples/jsm/controls/OrbitControls.js');
			if (disposed) return;

			scene = new three.Scene();
			scene.background = new three.Color(0x14171c);

			camera = new three.PerspectiveCamera(45, 1, 0.1, 5000);
			camera.position.set(0, -260, 200);
			camera.up.set(0, 0, 1);

			renderer = new three.WebGLRenderer({ antialias: true });
			renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
			container.appendChild(renderer.domElement);

			controls = new OrbitControls(camera, renderer.domElement);
			controls.enableDamping = true;

			scene.add(new three.AmbientLight(0xffffff, 0.45));
			const key = new three.DirectionalLight(0xffffff, 0.9);
			key.position.set(-1, -1.4, 2);
			scene.add(key);

			const observer = new ResizeObserver(resize);
			observer.observe(container);
			resize();
			ready = true;

			renderer.setAnimationLoop(() => {
				controls.update();
				renderer.render(scene, camera);
			});

			return () => observer.disconnect();
		})();

		return () => {
			disposed = true;
			renderer?.setAnimationLoop(null);
			renderer?.dispose();
			mesh?.geometry?.dispose();
			mesh?.material?.dispose();
			renderer = null;
		};
	});

	function resize() {
		if (!renderer || !camera) return;
		const { clientWidth: w, clientHeight: h } = container;
		if (!w || !h) return;
		renderer.setSize(w, h, false);
		camera.aspect = w / h;
		camera.updateProjectionMatrix();
	}

	// Rebuild whenever the DEM changes or any mesh-shaping setting changes. Deliberately does
	// NOT depend on the fetch settings — those invalidate the DEM itself, which is caught here
	// by `app.dem` going null.
	$effect(() => {
		const dem = app.dem;
		const m = app.settings.model;
		void m.modelHeight;
		void m.baseHeight;
		void m.exaggeration;
		void m.mmPerPixel;
		void m.seaLevelCap;
		if (!ready) return;
		untrack(() => void rebuild());
	});

	async function rebuild() {
		const dem = app.dem;
		if (!renderer || !dem) {
			clearMesh();
			return;
		}
		busy = true;
		try {
			const values = await app.ensureElevation();
			if (!values || app.dem?.demId !== dem.demId) return;
			clearMesh();
			mesh = buildMesh(values, dem.width, dem.height);
			scene.add(mesh);
			frameCamera();
		} finally {
			busy = false;
		}
	}

	function clearMesh() {
		if (!mesh) return;
		scene.remove(mesh);
		mesh.geometry.dispose();
		mesh.material.dispose();
		mesh = null;
	}

	function buildMesh(values: Float32Array, width: number, height: number) {
		const step = Math.max(1, Math.ceil(Math.max(width, height) / PREVIEW_MAX));
		const cols = Math.floor((width - 1) / step) + 1;
		const rows = Math.floor((height - 1) / step) + 1;

		const { modelHeight, baseHeight, exaggeration, mmPerPixel, seaLevelCap } = app.settings.model;

		// Pass one: exaggerate and cap, and find the extremes of the result. The normalisation
		// has to use the post-cap range, exactly as the server does.
		const z = new Float32Array(cols * rows);
		let lo = Infinity;
		let hi = -Infinity;
		for (let r = 0; r < rows; r++) {
			for (let c = 0; c < cols; c++) {
				let v = values[r * step * width + c * step] * exaggeration;
				if (seaLevelCap && v < 0) v = 0;
				if (!Number.isFinite(v)) v = 0;
				z[r * cols + c] = v;
				if (v < lo) lo = v;
				if (v > hi) hi = v;
			}
		}

		const span = hi - lo;
		const scale = span > 0 ? modelHeight / span : 0;
		const mmX = cols * step * mmPerPixel;
		const mmY = rows * step * mmPerPixel;

		const geometry = new three.PlaneGeometry(mmX, mmY, cols - 1, rows - 1);
		const position = geometry.attributes.position;
		for (let i = 0; i < z.length; i++) {
			// PlaneGeometry runs top row first; the DEM's first row is north. Flipping the row
			// index here keeps north away from the camera in the default view.
			const r = rows - 1 - Math.floor(i / cols);
			const c = i % cols;
			position.setZ(i, (z[r * cols + c] - lo) * scale + baseHeight);
		}
		position.needsUpdate = true;
		geometry.computeVertexNormals();

		const material = new three.MeshStandardMaterial({
			color: 0x9aa7b4,
			roughness: 0.85,
			metalness: 0.05,
			flatShading: false
		});
		return new three.Mesh(geometry, material);
	}

	function frameCamera() {
		if (!mesh) return;
		const box = new three.Box3().setFromObject(mesh);
		const size = box.getSize(new three.Vector3());
		const centre = box.getCenter(new three.Vector3());
		const reach = Math.max(size.x, size.y, size.z) * 1.6;
		camera.position.set(centre.x, centre.y - reach, centre.z + reach * 0.7);
		controls.target.copy(centre);
		controls.update();
	}
</script>

<div class="model" bind:this={container}>
	{#if !app.dem}
		<p class="placeholder">No terrain loaded.</p>
	{:else if busy}
		<p class="placeholder">Building preview…</p>
	{/if}
</div>

<style>
	.model {
		position: relative;
		width: 100%;
		height: 100%;
		background: #14171c;
		overflow: hidden;
	}
	.placeholder {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		margin: 0;
		color: var(--text-dim);
		font-size: 0.82rem;
		pointer-events: none;
	}
</style>
