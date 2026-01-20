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
			[50,20],
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
	initialize_earth_engine()

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



from skimage import transform

def map_label_elevation(img,im, size=500):
	
	img_map = img*0.0
	for x in map_labels:
		img_map[img == x[0]] =  x[1]


	img_map2  = img_map * 0.1
	img_map2  = img_map2 + transform.resize(im, img_map2.shape, anti_aliasing=True)-1
	#scale shape to make maximum dims 1000
	shape_out = img_map2.shape
	outsize = np.array(shape_out)/max(shape_out)*size
	img_map2 = transform.resize(img_map2, outsize, anti_aliasing=True)
	img_map2  = img_map2.round(1).clip(0.01)

	return img_map2