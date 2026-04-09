"""
Main module
"""
from itertools import product

import os 
import numpy as np
import pandas as pd

from scipy import ndimage as ndi
from scipy.ndimage import rotate

from skimage import morphology
from skimage.transform import resize
from skimage.draw import line_aa, polygon2mask

from PIL import Image

import h5py

from shapely.geometry import Polygon,MultiPolygon
from descartes import PolygonPatch

# Image and Visualization Tools
import matplotlib.pyplot as plt
import matplotlib.cm as cm

from skimage.transform import resize

import numpy2stl as np2stl
from geo2stl.geo2stl import proj_map_geo_to_2D

class DEM:
    def __init__(self, root=None, geo_bounds=None, data=None):

        self.data = None
        if root is not None:
            self.data = get_dem_geo(root, geo_bounds)
        else:
            if data is not None:
                self.data = data

        self.bounds = geo_bounds
        self.lines = []
        self.points = []
        self.polygons = []
        self.rotation = 0

    def draw(self):

        bounds = np.array(self.bounds)
                        
        fig=plt.figure(figsize=(10, 10))
        ax=fig.add_axes([0.1,0.1,0.8,0.8])
        ax.grid(True) 

        DEM = self.data.copy()
        if self.rotation != 0:
            print(".")
            DEM = rescale(DEM, DEM.shape[-1]*2)
            DEM = rotate(DEM,self.rotation, reshape=False, cval=-100000)
            DEM = DEM[:,~np.any(DEM<-1000,axis=0)]
            DEM = DEM[~np.any(DEM<-1000,axis=1),:]

            bounds = None

            DEM[DEM<0.001] = 0
            DEM = DEM[:,~np.all(DEM==0,axis=0)]
            DEM = DEM[~np.all(DEM==0,axis=1),:]

        else:
            ax.set_xlim(bounds[3],bounds[2])
            ax.set_ylim(bounds[1],bounds[0])       
            bounds = bounds[[3,2,1,0]]     

        #DEM = DEM.clip(*np.percentile(DEM.ravel(),[.1,99.9]))
        r = 1        
        ax.imshow(DEM[::r,::r],cmap = "jet",aspect= 'equal',extent = bounds)

        for line in self.lines:  line.draw()

        return DEM

    def save_stl(self,fn):
        print("not ready")
        #numpy2stl.numpy2stl(self.data, fn, solid=True, mask_val=0 )
    
    def resize(self,outsize=0):
        DEM = self.data
        if outsize > 0:
            res = np.array(outsize / np.amax(DEM.shape))
            DEM = resize(DEM, np.round(np.array(DEM.shape) * res),order=3)
        self.data = DEM

    def mask_region(self, polygon):
        self.data = mask_region(self.data, self.bounds, polygon)

## Utility functions

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

##############################
##        Read Data         ##
##############################
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


## ### ## DEM EDITING 
def embed_lines(DEM, pts_pix, res=1, diameter=2):     
    
    im_lines = np.zeros(DEM.shape, dtype=np.uint8)
    for x,y in pts_pix:
        x = x*res; y = y*res
        for i in range(len(x)-1):
            rr, cc, val = line_aa(int(round(y[i])), int(round(x[i])), int(round(y[i+1])), int(round(x[i+1])))
            rr[rr>=DEM.shape[0]] =  DEM.shape[0]-1
            cc[cc>=DEM.shape[1]] =  DEM.shape[1]-1
            
            im_lines[rr, cc] = val*255
                
    im_lines = morphology.binary_dilation(im_lines, morphology.disk(1))         
    #DEM[im_lines>0] = DEM[im_lines>0] - (.15 * np.max(DEM)-np.min(DEM))
    return im_lines

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

def get_mainland(mat):
    land = mat>0
    ccs,l_max = ndi.label(land)
    component_sizes = np.bincount(ccs[ccs>0])
    main_land = ccs == np.argmax(component_sizes)
    return main_land


def rescale(mat, sz_out=1000, scale=1):
    
    sz = np.array(mat.shape) 
    scale = sz_out / max(sz)
    sz = sz * scale
    sz = sz.astype(int)
    mat2 = resize(mat*1.,sz)
    mat2 = mat2 * scale * 1.
    
    return mat2

def adjust_hist(mat_adj):
    print("Adjusting Histogram")
    
    mat2 = mat_adj.copy()
    x = mat2[mat2>0]
    #x1 = [0,5, 10,25, 35, 50,      75,90,95,100]
    #x2 = [0,20,30,40, 45, 50,      60,70,80,100]
    
    x1 = np.linspace(0,100,15)
    x11 = (x1-50)/8
    x2 = 100/(1 + np.exp(-x11))
    
    y1 = np.percentile(x,x2)
    f = np.interp(x, y1, x1)
    mat2[mat2>0] = f
    
    plt.plot(x2,x1,"-o")
    plt.plot(x2,y1/y1[-1]*100, "-o")
    #xt = np.linspace(0,y1[-1],50)
    #plt.plot(xt, np.interp(xt, y1, x2) , "-o")
    return mat2

def DEM2STL(DEM, NSEW, rotation=0, n=1, fn=None):
       
    mat = DEM.data

    if n is not None:
        sz_out = 500*n
        mat = rescale(mat, sz_out=sz_out)

    DEM.data = mat

    ####
    mat_adj = proj_map_geo_to_2D(mat,NSEW)
    mat_adj= mat_adj.round(2)
    
    if rotation != 0:
        mat_adj = rotate(mat_adj,rotation, reshape=False)

        #mat_adj = mat_adj[:,~np.any(np.isnan(mat_adj),axis=0)]
       
    #mat_adj = mat_adj**0.5
    plot_dist(mat_adj)
    mat_adj = mat_adj[::-1,...] + 1 

    ## Save to STL
    if fn is not None:
        triangles = np2stl.array_to_mesh( mat_adj )
        solid = np2stl.Solid(triangles)
        #solid2 = np2stl.simplify_object_3D(solid)
        solid.save_stl(fn)
        plt.close("all")
    
def plot_dist(M): 

    fig,axs = plt.subplots(1,1)

    M = M.clip(*np.percentile(M.ravel(),[1,99]))
    axs.imshow(M,cmap="jet")
    #axs[1].hist(M[M>0].ravel(),50)    

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