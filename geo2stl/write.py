import os

import numpy as np
from numpy2stl import array_to_mesh, triangles_to_facets, writeSTL

def savefile(out_dir, name, im ):

		im = im.copy()
		if not os.path.isdir(out_dir): 
			print("folder not found")
			return
		
		if im.min()< 0.001:
			print("warning values less than zero")
			return 

		out_dir = out_dir + "/" + name 
		os.makedirs(out_dir,exist_ok=True)

		filename = out_dir + "/" + name + ".npy"
		save_im(filename, im)

		filename = out_dir + "/" + name + ".stl"
		save_stl(filename, im)


def save_im(filename, im):
		if os.path.isfile(filename):
			filename = filename.replace(".npy", "_1.npy")
		print("File already exists, saving as " + filename)
		np.save(filename, im)

def save_stl(filename, im):

		im = im[::-1]
		
		tri = array_to_mesh(im)
		facets = triangles_to_facets(tri)

		
		if os.path.isfile(filename):
			filename = filename.replace(".stl", "_1.stl")
		
		print("Saving STL")
		writeSTL(facets, filename)









     