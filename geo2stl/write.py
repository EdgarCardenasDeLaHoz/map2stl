import os



def savefile(out_dir, name, im2 ):

	im2 = im2.copy()

	if im2.min()< 1:
		print("warning values less than zero")
		#return 
	
	if not os.path.isdir(out_dir): return

	out_dir = out_dir + "/" + name 
	os.makedirs(out_dir,exist_ok=True)

	print("Saving Image")

	filename = out_dir + "/" + name + ".npy"
	if os.path.isfile(filename):
		filename = filename.replace(".npy", "_1.npy")
		print("File already exists, saving as " + filename)
	np.save(filename, im2)

	im2 = im2[::-1]
	
	tri = numpy2stl(im2)
	facets = triangles_to_facets(tri)

	filename = out_dir + "/" + name + ".stl"
	if os.path.isfile(filename):
		filename = filename.replace(".stl", "_1.stl")
	
	print("Saving STL")
	writeSTL(facets, filename)
     