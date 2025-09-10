import numpy as np
from PIL import Image
import re
import os

from skimage import io


def parse_extent_from_filename(filename):
	match = re.search(r'n([-\d.]+)_s([-\d.]+)_w([-\d.]+)_e([-\d.]+)', filename)
	if match:
		n, s, w, e = map(float, match.groups())
		return (n, s, e, w)
	else:
		raise ValueError(f"Could not parse extent from {filename}")

def intersect_bbox(bbox1, bbox2):
	N1, S1, E1, W1 = bbox1
	N2, S2, E2, W2 = bbox2
	N = min(N1, N2)
	S = max(S1, S2)
	E = min(E1, E2)
	W = max(W1, W2)
	if N > S and E > W:
		return (N, S, E, W)
	return None

def crop_tile_np(image_array, tile_bbox, crop_bbox):
	tile_N, tile_S, tile_E, tile_W = tile_bbox
	crop_N, crop_S, crop_E, crop_W = crop_bbox

	height, width = image_array.shape
	lat_per_pixel = (tile_N - tile_S) / height
	lon_per_pixel = (tile_E - tile_W) / width

	y1 = int((tile_N - crop_N) / lat_per_pixel)
	y2 = int((tile_N - crop_S) / lat_per_pixel)
	x1 = int((crop_W - tile_W) / lon_per_pixel)
	x2 = int((crop_E - tile_W) / lon_per_pixel)

	return image_array[y1:y2, x1:x2]

def parse_extent_from_filename(filename):
	match = re.search(r'n([-\d.]+)_s([-\d.]+)_w([-\d.]+)_e([-\d.]+)', filename)
	if match:
		parts = match.groups()
		# Remove trailing dots and convert to float
		try:
			n, s, w, e = [float(p.rstrip('.')) for p in parts]
		except ValueError:
			raise ValueError(f"Could not convert parts to float: {parts}")
		return (n, s, e, w)
	else:
		raise ValueError(f"Could not parse extent from filename: {filename}")

def stitch_tiles_no_rasterio(tile_files, target_bbox):
	print("==== Stitching tiles without rasterio ====")
	print(f"Target bounding box: {target_bbox}")
	

	rows = {}

	for fn in tile_files:
		tile_bbox = parse_extent_from_filename(os.path.basename(fn))
		#print(f"Tile: {fn}\n Parsed extent: {tile_bbox}")

		intersection = intersect_bbox(tile_bbox, target_bbox)
		#print(f" Intersection: {intersection}")

		if not intersection:
			#print(" Skipping due to no intersection.")
			continue

		try:
			image_array = io.imread(fn)	
		except Exception as e:
			print(f"⚠️ Failed to open {fn}: {e}")
			continue

		cropped = crop_tile_np(image_array, tile_bbox, intersection)
		row_key = intersection[0]
		rows.setdefault(row_key, []).append((intersection[3], cropped))  # use W for sorting

	if not rows:
		print("==== No tiles matched ====")
		return None

	stitched_rows = []
	for N in sorted(rows.keys(), reverse=True):
		tiles = sorted(rows[N], key=lambda t: t[0])
		row = np.hstack([img for _, img in tiles])
		stitched_rows.append(row)

	final_image = np.vstack(stitched_rows)
	print("✅ Finished stitching without rasterio.")
	return final_image


def proj_map_height(mat,NSEW):
    n,s,e,w = NSEW

    m1, n1 = mat.shape
    xv,yv = np.meshgrid(range(n1),range(m1))
    
    xv = ((xv/n1)-0.5) * (e-w)
    xv = np.deg2rad(xv)
        
    yv = ((1-yv/m1)-0.5) * (n-s)

    yv = np.deg2rad(yv)
    #zv = xv_c * yv_c
    
    zv = np.cos(xv) * np.cos(yv)  # * (12756) 
    
    zv = zv * m1/(n-s) * 180/np.pi
    
    #zv = zv - np.min(zv)
        
    return zv


def proj_map_geo_to_2D(mat,NSEW, clip_out=True):
		
		lat = NSEW[[0,1]]
		lon = NSEW[[2,3]]

		m, n = mat.shape
		xv,yv = np.meshgrid(range(n),range(m))

		xc = (n-1)/2
		yc = (m-1)/2
		xv_c = (xv - xc).astype(int)
		yv_c = (yv - yc).astype(int)

		lat_v = np.linspace(lat[0],lat[1],m)
		lat_v = np.deg2rad(lat_v[:,None])
		xv_adj = xv_c * np.cos(lat_v )

		xv2 = (xv_adj + xc).astype(int)
		yv2 = (yv_c + yc).astype(int)

		mat_adj = mat*0.0 
		mat_adj[:] = np.nan
		mat_adj[yv2, xv2] = mat[yv, xv]
		
		y1,y2 = np.min(yv2),np.max(yv2)
		x1,x2 = np.min(xv2),np.max(xv2)
		
		mat_adj = mat_adj[y1:y2,x1:x2]

		if clip_out:
			mat_adj = mat_adj[:,~np.any(np.isnan(mat_adj),axis=0)]

		return mat_adj  

def mat2coor(limits, matsize, index):
  [x1,x2,y1,y2] = index

  xs = np.array([x1,x2])
  xs = xs / matsize[0]
  xs = (xs * limits[1]) + limits[0]


  ys = np.array([y1,y2])
  ys = ys / matsize[1]
  ys = (ys * (limits[3]-limits[2])) + (limits[2])

  print(xs,ys)

  coor = [xs[0], xs[1], ys[0], ys[1]]

  return coor


