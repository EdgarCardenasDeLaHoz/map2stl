
import os 
import sys
import numpy as np
import pandas as pd
from shapely.geometry import Polygon
import osmnx as ox
from skimage import filters,transform


from .buildings import *
from .dem2stl import *
from .osm2stl import *


from numpy2stl.numpy2stl import simplify as simp

def get_building_model(gdf, scale):

    building_poly = get_polygons(gdf)
    tris = triangulate_prism(building_poly)

    vertices, faces = np2stl.vertices_to_index(tris)
    vertices[:,[1,0]] = vertices[:,[0,1]]*1000

    vertices[:,2] = vertices[:,2]*scale

    return vertices, faces


def get_landspace_model(data, bounds_NW=None, scale=1, simplify=True):

    ####################
    facet = np2stl.numpy2stl(data)
    solid = np2stl.Solid(facet)
    
    vx = solid.vertices.copy().astype(float)
    fs = solid.faces.copy()

    if bounds_NW is not None:
        im_lims = ((0,data.shape[0]),(0,data.shape[1])) 
        vx = reposition_dem(vx, im_lims, bounds_NW )
        vx[:,[0,1]] = vx[:,[0,1]]*1000

    if simplify:
        fs = simp.simplify_mesh_surfaces(vx,fs)

    ########################
    vx[:,[0,1]] = vx[:,[0,1]]    
    vx[:,2] = vx[:,2] * scale    

    return vx, fs

def get_bounds_model(gdf, scale):
    
    
    x_ = gdf["geometry"]
    xy_list = np.array([[x.centroid.x,  x.centroid.y] for x in x_])
    Nc,Wc = xy_list.T
    c,d,a,b= Nc.max(),Nc.min(),Wc.max(),Wc.min()    

    prism_dict = {}
    prism_dict['z1'] = 100
    prism_dict['z0'] = 0
    prism_dict['points'] = np.array([[c,a],[c,b],[d,b],[d,a],[c,a]]).T

    tris = triangulate_prism([prism_dict])
    vertices, faces = np2stl.vertices_to_index(tris)
    vertices[:,[1,0]] = vertices[:,[0,1]]
    vertices[:,2] = vertices[:,2] * scale
    vertices = vertices*1000

    return vertices, faces


def get_bbox(gdf):
    x_ = gdf["geometry"]
    xy_list = np.array([[x.centroid.x,  x.centroid.y] for x in x_])
    Nc,Wc = xy_list.T
    bounds_rc = Nc.max(),Nc.min(),Wc.max(),Wc.min()    

    b = bounds_rc
    bounds_rc = [[b[2],b[3]],[b[1],b[0]]]

    return bounds_rc



def get_bounds_(gdf, im_shape, coor_lims):
    x_ = gdf["geometry"]
    xy_list = np.array([[x.centroid.x,  x.centroid.y] for x in x_])
    Nc,Wc = xy_list.T
    bounds_rc = Nc.max(),Nc.min(),Wc.max(),Wc.min()    

    b = bounds_rc
    bounds_rc = [[b[2],b[3]],[b[1],b[0]]]
    
    #Flip these commands to that mx and min happen before conversion 

    im_lims = np.array(((0,im_shape[0]),(0,im_shape[1])))
    Yc, Xc = coor2im(coor_lims, im_lims, xy_list)
    bounds = Yc.max(),Yc.min(),Xc.max(),Xc.min()


    return bounds, bounds_rc

def get_base_height(gdf, im, coor_lims):

    x_ = gdf["geometry"]

    ###########################
    xy_list = []
    for x in x_:
      xy_list.append([x.centroid.x,  x.centroid.y])
    xy_list = np.array(xy_list)

    ###########################
    im_lims = np.array([(0,im.shape[0]),(0,im.shape[1])])
    Nc, Wc = coor2im(coor_lims, im_lims, xy_list)

    Nc = Nc.clip(0,im.shape[0]-1)
    Wc = Wc.clip(0,im.shape[1]-1)
    
    ############################
    H = im[Nc, Wc] 

    return H

def get_bounds(gdf, im, coor_lims):
     
    x_ = gdf["geometry"]
    
    xy_list = [[x.centroid.x,  x.centroid.y] for x in x_]
    xy_list = np.array(xy_list)

    im_lims = np.array([(0,im.shape[0]),(0,im.shape[1])])
    Nc, Wc = coor2im(coor_lims, im_lims, xy_list)

    #################
    Nx,Sx = Nc.max(),Nc.min()
    Ex,Wx = Wc.max(),Wc.min()
    
    return Nx,Sx,Ex,Wx
    
  
def coor2im(coor_lims, im_lims, xy_list, asint=True):
    
    N0,N1 = coor_lims[0]
    W0,W1 = coor_lims[1]
    X0,X1 = im_lims[0]
    Y0,Y1 = im_lims[1]

    Ncoor = map( N0,N1, X0, X1, xy_list[:,1])
    Wcoor = map( W0,W1, Y0, Y1, xy_list[:,0])
    
    if asint:
        Ncoor,Wcoor = Ncoor.astype(int), Wcoor.astype(int)

    return Ncoor, Wcoor

def crop_image_bounds(im, bounds):
    Nx,Sx, Ex,Wx = bounds
        
    ###################### 
    data =  im[int(Sx):int(Nx), int(Wx):int(Ex)]
    data = 1.0 * data

    rho = 0.2
    outshape = np.array(data.shape)*rho
    data = transform.resize(data, outshape)

        
    return data


def reposition_dem(vx, im_lims, coor_lims):

  N0,N1 = coor_lims[0]
  W0,W1 = coor_lims[1]
  X0,X1 = im_lims[0]
  Y0,Y1 = im_lims[1]

  #if N1<N0: N1,N0 = N0,N1
  #if W1<W0: W1,W0 = W0,W1  
  x,y = vx[:,1],vx[:,0]

  imcoor = np.array((y,x)).T*1.
  Ncoor = map( X0*1., X1*1., N0,N1, imcoor[:,1])
  Wcoor = map( Y0*1., Y1*1., W0,W1, imcoor[:,0])
  
  vx[:,0], vx[:,1] = Ncoor, Wcoor

  return vx 
#########################

def map(low_in, high_in, low_out, high_out, qx):

  ix = (qx - low_in)
  ix = (ix / (high_in - low_in))

  ix = ix * (high_out - low_out)
  ix = ix + low_out

  return ix

from shapely.geometry import polygon

def polygon_to_perimeter(poly):
    
    poly = polygon.orient(poly)
    
    verts,peri = [],[]
    n_v = 0
    exter = np.array(poly.exterior.coords)
    exter = exter[:-1]
    verts.extend(exter)
    peri.append( np.arange(len(exter) + n_v ))
    n_v = len(exter) + n_v 
    
    
    inter = poly.interiors
    for p in inter:
        pts = p.coords[:-1]
        verts.extend( pts )
        peri.append( np.arange(len(pts)) + n_v )
        n_v = len(pts) + n_v             
               
    verts = np.array(verts)
    
    perimeters = []
    for line_idx in peri:
        line = verts[line_idx]
        
        angles = get_perimeter_angles( line) 
        simpified_line = np.array(line_idx[  (angles < 179) | (angles > 181) ])
        perimeters.append(simpified_line)
    

    return verts,perimeters

def polygon_to_prism(polygons,heights,base_val=0):
    all_triangles = []

    for n,poly in enumerate(polygons):
        print(n)
        #if poly.area < 500: continue        
        
        verts, peri = polygon_to_perimeter(poly)
        verts = np.concatenate((verts, verts[:,0:1]*0),axis=1)
        
        verts[:,2] = heights[n]
        try:
            _, faces = np2stl.simplify_surface(verts, peri)
        except: 
            continue
        
        #    print(verts)
        ## Add Z value
        top_tris = verts[faces]
        all_triangles.append( top_tris )
        wall_tris = np2stl.perimeter_to_walls(verts, peri, floor_val=base_val)
        all_triangles.append( wall_tris )

    return all_triangles

def shapely_to_buildings(shp_poly, z0=1,z1=39,polygons=None):

    if polygons is None:    polygons = []
        
    for poly in shp_poly.geoms:
        p = {}
        p['roof_height'] = z1
        p['base_height'] = z0
        p['points'] = np.array(poly.exterior.coords).T
        polygons.append(p)
        
    return polygons

def triangulate_prism(polygons):

    triangles = []

    for _,p in enumerate( polygons ):

        roof = p['z1'] 
        base = p['z0'] 
        vert = np.array(p['points']).T

        if (np.isclose(vert[0],vert[-1]).all()):   
            vert = vert[:-1]

        zdim = np.zeros((len(vert),1)) + roof
        vert = np.concatenate([vert, zdim],axis=1)        
        tri = np2stl.polygon_to_prism(vert, base_val=base)
        triangles.append( tri )

    triangles = np.concatenate(triangles)   
    return triangles

def boundry_to_poly(GEO_poly):
    pts = np.array(GEO_poly.exterior.coords).T
    p = {"points":pts,"roof_height":0,"base_height":-30}
    polygons = [p]

    return polygons

def get_waterways( GEO ):
    
    ftpt = ox.footprints_from_polygon(GEO, footprint_type="natural")    
    
    x = ftpt[ftpt["natural"]=="water"]
    x = x.dropna(axis=1, how='all')
    x = x[["geometry","name","waterway","natural"]]
    areas = [i["geometry"].area*10000000 for n,i in x.iterrows()]
    x["areas"] = areas
    x = x[x["areas"]>1]
    
    polys = [ i["geometry"].intersection(GEO) for n,i in x.iterrows()]
    x["geometry"] = polys
    x = ox.project_gdf(x)
    return x 