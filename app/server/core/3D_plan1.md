Plan: Building Height Estimation — Data Sources + CNN Pipeline
OSM heights are incomplete (3-tier fallback: height tag → building:levels * 4 → 10m default). We'll exhaust ground-truth sources first, then build CNN prediction, then STL→heightmap→AI infill. Backend/session API only.

Phase 1: Exhaust Ground-Truth Data Sources
1.1 Microsoft Global Building Footprints (free, no key, global)
ML-derived height estimates via Planetary Computer STAC API. New module app/server/core/msft_buildings.py — spatial join with OSM footprints to fill missing heights.

1.2 Google 3D Tiles (you have Maps API key)
Photogrammetric 3D city models. Excellent quality for Barcelona, good for Granada. New module app/server/core/google_3d.py — fetch glTF mesh tiles, ray-cast per building footprint to get roof−ground height. Note: free tier is 1000 req/month; heavier use has costs.

1.3 EU Copernicus Building Height (free, Europe only)
10m resolution raster. Covers Barcelona + Granada perfectly. GeoTIFF download + cache.

1.4 DLR WSF3D (free, global, ~12m)
Derived from TanDEM-X SAR. Global coverage including Cartagena. GeoTIFF tiles.

1.5 IGN PNOA LiDAR (free, Spain only)
~1-4 pts/m² point clouds for Barcelona + Granada. LAZ → DSM/DTM → building height. Deps: laspy.

1.6 Shadow-based Estimation (zero-cost, works everywhere)
Shadow length + sun angle from satellite metadata. ±3-5m accuracy. Works as supplementary signal for cities like Cartagena with zero data.

1.7 Height Priority Cascade — modify _fill_heights() in osm.py:

OSM height tag → 2. building:levels * 4 → 3. Google 3D Tiles → 4. LiDAR → 5. Microsoft Footprints → 6. Copernicus/WSF3D → 7. Shadow → 8. CNN prediction → 9. Default 10m
Phase 2: CNN Building Height from Satellite
2.1 Pretrained — quick start
Depth Anything V2 (small, ~300MB) for monocular depth. Calibrate relative depth to absolute metres using known OSM heights in the same tile (linear regression). New module app/server/core/height_predict.py. Session: s.predict_heights(model="pretrained")

2.2 Custom U-Net — quality
U-Net + EfficientNet-B4 encoder, 256×256 tiles at ~5m/px. L1 + gradient loss. Train on paired (satellite, height_map) tiles from Phase 1 ground truth. New module height_train.py + notebook. Session: s.train_height_model(cities=["Barcelona"], epochs=50)

Approach	Expected MAE	Speed	Training Data	Complexity
Depth Anything V2 + calibration	~5-10m	<1s/tile	None (zero-shot)	Low
Custom U-Net	~2-4m	<1s inference, hours to train	~1000 paired tiles	Medium
Shadow-based	~5-15m	Very fast	None	Low
Phase 3: STL → Heightmap → AI Infill
3.1 STL Import — new module app/server/core/stl_import.py. Trimesh load → Z-axis ray-cast → 2D height array with NaN outside mesh. Session: s.load_stl("cathedral.stl", bbox=(lat1,lon1,lat2,lon2))

3.2 AI Infill — fill NaN regions using context from the known area + satellite RGB

Approach	Quality	Speed	Data Needed	Recommended
Partial Conv U-Net	Good for smooth fill	<1s	~500 tiles	Start here
GAN conditioned on satellite	Better for buildings	~2s	~1000 tiles	Evolve to this
Diffusion inpainting	Best quality	~10s	~2000 tiles	Future
New module height_infill.py. Session: s.infill_heights(method="pconv")

Key Files
Modify: osm.py (_fill_heights cascade), terrain_session.py (new methods), requirements.txt (add torch, torchvision, timm, laspy)
New: core/msft_buildings.py, core/google_3d.py, core/height_predict.py, core/height_train.py, core/stl_import.py, core/height_infill.py, core/shadow_height.py, core/lidar.py, routers/height.py
New notebooks: Train_Height_CNN.ipynb, Height_Prediction.ipynb, STL_Infill.ipynb
Model storage: models/ directory (gitignored)
Verification
Coverage: compare OSM-only vs multi-source height coverage for Barcelona (% of buildings with real data)
Pretrained CNN: MAE on Barcelona test tiles
Custom CNN: MAE on Granada (held-out city)
STL roundtrip: load test STL → heightmap → verify bbox alignment
Infill: mask 50% of known heightmap, infill, measure RMSE
Phase Dependencies
Phase 1 → Phase 2 (ground truth needed for training data) → Phase 3 (trained model used for infill). Within Phase 1, sources 1.1-1.6 are independent and can be built in parallel.

