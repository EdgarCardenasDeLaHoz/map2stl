import ee
import requests
import numpy as np
from PIL import Image
from io import BytesIO


def initialize_earth_engine():
	try:
		ee.Initialize()
	except Exception as e:
		print(e)
		ee.Authenticate()
		ee.Initialize()		


def get_aquatic_regions(N,S,E,W, dataset="esa", scale=1000):

  initialize_earth_engine()
  img = fetch_bbox_image(N, S, E, W, scale=scale, dataset=dataset)


  if dataset=="esa":
    # Water class is 80
    img[img[:,:,1]==0,0] = 0
    img = img[:,:,0] 
    img[img==80] = 0

  elif dataset=="jrc":

    # Water class is >0
    print(img.shape)
    #img = img.clip(0,1)
    #img = img**2
  return img


def fetch_bbox_image(N, S, E, W, scale=5000, dataset="esa"):

	crs="EPSG:4326"
	# Bounding box as an EE geometry
	region = ee.Geometry.Rectangle([W, S, E, N], proj="EPSG:4326", geodesic=False)
	if dataset=="esa":
		dataset_id = "ESA/WorldCover/v100/2020"
		band = "Map"
	elif dataset=="jrc":
		dataset_id = "JRC/GSW1_3/GlobalSurfaceWater"
		band = "occurrence"
	
	landcover = ee.Image(dataset_id).select(band).clip(region)


	# Generate the download URL with the same CRS as the region
	url = landcover.getThumbURL({
		"scale": scale,
		"region": region,
		"format": "GEO_TIFF",
		"crs": crs  
	})

	# Fetch the image
	response = requests.get(url)
	img = Image.open(BytesIO(response.content))
	return np.array(img)