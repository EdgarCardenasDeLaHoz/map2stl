"""
Main module
"""
from itertools import product

import numpy as np
from scipy.ndimage import rotate
from skimage.transform import resize
from skimage import morphology
from skimage.draw import line_aa, polygon2mask

from PIL import Image

import os 
import numpy as np
import pandas as pd

from shapely.geometry import Polygon,MultiPolygon
from descartes import PolygonPatch

# Image and Visualization Tools
import matplotlib.pyplot as plt
import matplotlib.cm as cm

import h5py

class DEM:

    def __init__(self, Root, geo_bounds):

        self.data = get_dem_geo(Root, geo_bounds)
        self.bounds = geo_bounds
        self.lines = None
        self.points = None
        self.polygons = None

    def draw(self):
        draw_dem(self.data, self.bounds)
        if self.lines is not None:
            draw_lines(self.lines)

    def save_stl(self,fn):

        numpy2stl.numpy2stl(self.data, fn, solid= True, mask_val=0 )
    
    def resize(self,outsize=0):
        DEM = self.data
        if outsize > 0:
            res = np.array(outsize / np.amax(DEM.shape))
            DEM = resize(DEM, np.round(np.array(DEM.shape) * res))
        self.data = DEM

    def mask_region(self, polygon):
        self.data = mask_region(self.data, self.bounds, polygon)


def draw_dem(DEM, bounds):
                
        fig=plt.figure(figsize=(10, 10))
        ax=fig.add_axes([0.1,0.1,0.8,0.8])
        ax.grid(True) 
        ax.set_xlim(bounds[3],bounds[2])
        ax.set_ylim(bounds[1],bounds[0])
        bounds = bounds[[3,2,1,0]]

        ax.imshow(DEM,cmap = "jet",aspect= 'equal',extent = bounds)

def draw_lines(lines):  
    for line in lines:
        x,y = line.pts
        plt.plot(x,y,"g")
    return DEM

## Utility functions
def get_dem_geo(Root,geo_bounds):
    "Uses convention where Y is Long and X is Lat"

    North,South,East,West = geo_bounds

    T_X1,T_Y1,X1,Y1 = geo_2_tile_pixel(North,West)
    T_X2,T_Y2,X2,Y2 = geo_2_tile_pixel(South,East)
    X2 = (T_X2 - T_X1)*6000 + X2
    Y2 = (T_Y2 - T_Y1)*6000 + Y2

    DEM = get_section_h5(Root, (T_X1, T_Y1), (T_X2+1,T_Y2+1))               
    DEM = crop_region(DEM, (Y1, X1), (Y2, X2), 0)
    DEM = np.maximum(DEM,0)
    DEM = DEM - (np.min(DEM)*.9)
    DEM = DEM / 90.0 * 10
    return DEM

def crop_region(DEM,ul,lr,theta):
    """
    Given rasterized mosaic DEM and rectangle bounded by grid
    coordinates given by upper left coordinates `ul` and lower right
    coordinates `lr` crop and rotate image 
    """
    if not theta==0:
        DEM = rotate(DEM, theta, axes=(1, 0), reshape=True)
    DEM = DEM[ul[0]:lr[0], ul[1]:lr[1]]
    
    return DEM 

def mask_region(DEM, bounds, polygon, m_val=-1 ):

    if m_val==None:
        m_val = np.min(DEM)

    GeoStart = bounds[[0,3]]
    polygon = map_coor_2_pixel(polygon[::-1,:],GeoStart)
    polygon = simplify_polygon(polygon)
    mask = polygon2mask(DEM.shape, polygon)
    DEM[np.logical_not(mask)] = m_val
        
    return DEM

def simplify_polygon(polygon):

    bnd_pts = polygon.T
    bnd_pts = np.round(bnd_pts)
    _, ind = np.unique(bnd_pts, axis=0, return_index=True)
    bnd_pts = bnd_pts[np.sort(ind)]

    angles = get_perimeter_angles( bnd_pts ) 
    curved = (angles < 175) | (angles > 185)
    polygon = bnd_pts[curved, ::-1]

    return polygon


def get_perimeter_angles(line_2D):

    if line_2D.shape[1]==3:
        line_2D = line_2D[:,0:2]
        
    line_wrapped = np.concatenate([[line_2D[-1]],line_2D,[line_2D[0]]])
    ba = line_wrapped[0:-2] - line_wrapped[1:-1]
    bc = line_wrapped[2::] - line_wrapped[1:-1]
    angles = get_angle_vectors(bc, ba )
   
    return angles

def get_angle_vectors(ba, bc):

    ba = ba / np.array(np.linalg.norm(ba, axis=1))[:,None]
    bc = bc / np.array(np.linalg.norm(bc, axis=1))[:,None]
    dot_prod = np.sum(ba*bc, axis=1) 
    cross_prod = np.cross(ba,bc)

    angle = np.arctan2(cross_prod , dot_prod)
    angle = np.degrees(angle)
    angle[angle<0] += 360

    return angle

def get_section(root: str, ul: np.ndarray, lr: np.ndarray) -> np.ndarray:
    """
    Given root dir return rasterized mosaic DEM from rectangle bounded by grid
    coordinates given by upper left coordinates `ul` and lower right
    coordinates `lr` in the downampled image space.
    """
    TILE_SHAPE = 6000, 6000
    
    downshape = np.array(lr) - np.array(ul)
    outshape = downshape * np.array(TILE_SHAPE)

    out = np.zeros(outshape, "i2")
    for x, y in product(range(ul[0], lr[0]), range(ul[1], lr[1])):
        pathname = os.path.join(root, f"srtm_{x:02d}_{y:02d}.tif")
        
        try:
            out[(x - ul[0]) * TILE_SHAPE[0]:(x - ul[0]+1) * TILE_SHAPE[0],
                (y - ul[1]) * TILE_SHAPE[1]:(y - ul[1]+1) * TILE_SHAPE[1]
            ] = np.array(Image.open(pathname))[:TILE_SHAPE[0], :TILE_SHAPE[1]].T
        except FileNotFoundError:
            print(f"srtm_{x:02d}_{y:02d}.tif file not found")
            continue
    return out.T

def get_section_h5(root: str, ul: np.ndarray, lr: np.ndarray) -> np.ndarray:
    """
    Given root dir return rasterized mosaic DEM from rectangle bounded by grid
    coordinates given by upper left coordinates `ul` and lower right
    coordinates `lr` in the downampled image space.
    """
    TILE_SHAPE = 6000, 6000
    
    downshape = np.array(lr) - np.array(ul)
    outshape = downshape * np.array(TILE_SHAPE)

    out = np.zeros(outshape, "i2")
    for x, y in product(range(ul[0], lr[0]), range(ul[1], lr[1])):
        
        try:
            filename = os.path.join(root, "strm_data.h5")
            
            with h5py.File(filename, 'r') as fh:
                d_name = f"srtm_{x:02d}_{y:02d}"
                if d_name in fh.keys():
                    print(d_name)
                    data = fh[d_name][:]
                    data = np.array(data)
                else:
                    print(d_name + ": file not found")
                    continue
            
        except FileNotFoundError:
            print(f"srtm_{x:02d}_{y:02d}: file not found")
            continue
            
        X1 = (x - ul[0]) * TILE_SHAPE[0]
        X2 = (x - ul[0]+1) * TILE_SHAPE[0]
        Y1 = (y - ul[1]) * TILE_SHAPE[1]
        Y2 = (y - ul[1]+1) * TILE_SHAPE[1]   
        
        out[ X1:X2 , Y1:Y2 ] = data[:TILE_SHAPE[0], :TILE_SHAPE[1]].T
            
            
    out = out.T
    return out

def map_coor_2_pixel(GeoLoc,GeoStart,round_val=False):
    
    tX1,tY1,pX1,pY1 = geo_2_tile_pixel( GeoStart[0],GeoStart[1],round_val )
    tX2,tY2,pX2,pY2 = geo_2_tile_pixel( GeoLoc[0],GeoLoc[1],round_val )
    
    px = (tX2 - tX1)*6000 + (pX2 - pX1)
    py = (tY2 - tY1)*6000 + (pY2 - pY1)
    
    return np.array((px,py))

def geo_2_tile_pixel(Lat=0,Long=0,round_val=True):

    "Produces convention where X is Long and Y is Lat"
    tilX = np.int16(np.floor(Long / 5) + 36 + 1)
    tilY = np.int16(np.floor(-Lat / 5) + 12 + 1)
    
    pixX = (Long /5 - np.floor(Long / 5))*6000
    pixY = (-Lat /5 - np.floor(-Lat / 5))*6000
    
    if round_val:
        pixX = np.int64(np.round(pixX))
        pixY = np.int64(np.round(pixY))
    
    return (tilX,tilY,pixX,pixY)

def tile_num_2_geo_coor(TX=0,TY=0,PX=0,PY=0):

    Long = 180 * (TX-37)/36
    Long += np.round(PX / 6000 * 5,4)        
    Lat = 60 * (TY-13)/12
    Lat += np.round(PY / 6000 * 5,4)
    
    return (-Lat,Long)


## ### ## DEM EDITING 
def embed_lines(DEM, pts_pix, res=1):     
    
    im_lines = np.zeros(DEM.shape, dtype=np.uint8)
    for x,y in pts_pix:
        x = x*res; y = y*res
        for i in range(len(x)-1):
            rr, cc, val = line_aa(int(round(y[i])), int(round(x[i])), int(round(y[i+1])), int(round(x[i+1])))
            im_lines[rr, cc] = val*255
                
    im_lines = morphology.binary_dilation(im_lines, morphology.disk(2))         
    DEM[im_lines>0] = DEM[im_lines>0] - (.15 * np.max(DEM)-np.min(DEM))
    return DEM

## 
"""
from PIL import ImageDraw, ImageFont

def get_text_image(x, y, text_str,im_shape,font=None):
    Text_im = np.zeros(im_shape)
    Text_im = Image.fromarray(Text_im)
    draw = ImageDraw.Draw(Text_im)
    draw.text((x, y),text_str,(255),font)
    return TEXT_im

def embed_text(DEM,x,y,text_str,fontsize):
    
    font = ImageFont.truetype("arial.ttf", fontsize)
    Text_im = np.array(get_text_image(x, y, text_str,DEM.shape,font))
    DEM[TEXT_im>0] = DEM[TEXT_im>0] - (.2 * np.max(DEM)-np.min(DEM))
    
    return DEM

##
"""
def get_bounds_geo(Coor,idx):
    
    #Get North, West 
    X = list(Coor.loc[idx,["GridX1","GridY1","Y1","X1"]])
    GeoStart = tile_num_2_geo_coor(*X)
    #Get South, East 
    X = list(Coor.loc[idx,["GridX1","GridY1","Y2","X2"]])
    GeoEnd = tile_num_2_geo_coor(*X)
    bounds = np.array( [GeoStart[0],GeoEnd[0],GeoEnd[1],GeoStart[1]])
    
    #bounds is north, south, east, west
    return bounds

