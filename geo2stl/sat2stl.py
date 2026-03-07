import ee
import requests
import numpy as np
from PIL import Image
from io import BytesIO

"""
10: Tree cover
20: Shrubland
30: Grassland
40: Cropland
50: Built-up
60: Bare / sparse vegetation
70: Snow and Ice
80: Permanent water bodies
90: Herbaceous Wetland
95: Mangrove
100: Moss and lichen 
"""

map_labels = [[10,5],
			[20,0],
			[30,0],
			[40,0],
			[50,10],
			[60,0],
			[70,0],
			[80,-10],
			[90,-5]]

def initialize_earth_engine():
	try:
		ee.Initialize()
	except Exception as e:
		print(e)
		ee.Authenticate()
		ee.Initialize()		


def get_aquatic_regions(N,S,E,W, dataset="esa", scale=1000, use_cache=True):

  initialize_earth_engine()
  img = fetch_bbox_image(N, S, E, W, scale=scale, dataset=dataset, use_cache=True)
  print("...")

  if dataset=="esa":
    # Water class is 80
    img[img[:,:,1]==0,0] = 0
    img = img[:,:,0] 
    img[img==80] = 0

  elif dataset=="jrc":

    # Water class is >0    
    #img = img.clip(0,1)
    #img = img**2
    pass

  return img


import os
import hashlib
import numpy as np
import requests
from io import BytesIO
from PIL import Image
import ee
import joblib  # Recommended for numpy arrays: pip install joblib

# Create a cache directory
CACHE_DIR = "ee_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

def fetch_bbox_image(N, S, E, W, scale=30, dataset="copernicus", use_cache=True):
    
    # 1. Create a unique hash based on all input parameters
    param_str = f"{N}_{S}_{E}_{W}_{scale}_{dataset}"
    cache_hash = hashlib.md5(param_str.encode()).hexdigest()
    cache_path = os.path.join(CACHE_DIR, f"{cache_hash}.jbl")

    # 2. Check if the file exists in cache
    if use_cache and os.path.exists(cache_path):
        print(f"Loading {dataset} from cache...")
        return joblib.load(cache_path)
    
    initialize_earth_engine()

    crs = "EPSG:4326"
    region = ee.Geometry.Rectangle([W, S, E, N], proj=crs, geodesic=False)

    # Dataset Registry
    datasets = {
        "esa": ("ESA/WorldCover/v100/2020", "Map"),
        "jrc": ("JRC/GSW1_4/GlobalSurfaceWater", "occurrence"),
        "copernicus": ("COPERNICUS/DEM/GLO30", "DEM"),
        "nasadem": ("NASA/NASADEM_HGT/001", "elevation"),
        "usgs": ("USGS/3DEP/10m", "elevation"),
			  "gebco": ("projects/sat-io/open-datasets/gebco/gebco_2023_grid", "elevation")   
		}
    
    if dataset not in datasets:
        raise ValueError(f"Dataset not recognized. Choose from: {list(datasets.keys())}")

    dataset_id, band = datasets[dataset]
    
	# Load and process the dataset
    if dataset == "copernicus":
        # We mosaic the collection into one image
        img = ee.ImageCollection(dataset_id).mosaic().select(band)
    else:
        # Standard images (ESA, NASADEM, JRC)
        img = ee.Image(dataset_id).select(band)

    image = img.toInt16().clip(region)
    # 3. Fetch from Earth Engine

    try:
        url = image.getThumbURL({
            "scale": scale,
            "region": region,
            "format": "GEO_TIFF",
            "crs": crs  
        })

        response = requests.get(url)
        if response.status_code != 200:
            raise Exception(f"EE Error: {response.text}")
        
        img_array = np.array(Image.open(BytesIO(response.content)))



        if use_cache:
            print("caching")
            joblib.dump(img_array, cache_path)
            
        return img_array

    except Exception as e:
        print(f"Request failed: {e}")
        return None

from skimage import transform

def map_label_elevation(img,im, size=500):
    
    if img is None:
        return im*0
    
    img_map = img*0.0
    for x in map_labels:
        img_map[img == x[0]] =  x[1]


    img_map2  = img_map *1.0
    img_map2  = img_map2 + transform.resize(im, img_map2.shape, anti_aliasing=True)
    #scale shape to make maximum dims 1000
    shape_out = img_map2.shape
    outsize = np.array(shape_out)/max(shape_out)*size
    img_map2 = transform.resize(img_map2, outsize, anti_aliasing=True)
    img_map2  = img_map2.round(0).clip(0.1)

    return img_map2