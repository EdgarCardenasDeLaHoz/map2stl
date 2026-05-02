import numpy as np 
import glob
import os

import cv2
from PIL import Image
from scipy.ndimage.measurements import minimum
from skimage import io, transform
import scipy.ndimage as ndi

import h5py 

import torch
import torch.nn as nn
import torch.nn.functional as F

from torch.utils import data
from torchvision import transforms
from torch.utils.data import Dataset, DataLoader
from skimage import morphology as mor

import matplotlib.pyplot as plt
import matplotlib.pyplot as plt

class SuperLoader():

    def __init__(self, loaderlist): 

        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        ## Variables for buffering 
        self.batchsize = 10
        self.loaderlist = loaderlist

        self.in_memory = []
        self.n_memory = 10
        if len(loaderlist)>0: 
            self.dataset = loaderlist[0].dataset

    def  __getitem__(self, idx):    
                
        ## Clear the buffer after selection?
        #if idx == (len(self)-1): self.in_memory = []    

        if  len(self.in_memory)==0:
            self.load_memory()

        self.in_memory[idx]

        I,T = [],[]
        for b in range(self.batchsize):         
            i = np.random.randint(len(self.in_memory))
            im_cat, (L,idx) = self.in_memory[i]
            im_cat = self.loaderlist[L].dataset.get_section(im_cat)
            if im_cat is None: continue

            I.append(im_cat[0])
            T.append(im_cat[1])

        
        I = np.array(I)
        T = np.array(T)
        I = torch.tensor(I).to(self.device).float()
        T = torch.tensor(T).to(self.device).float()

        return (I, T, idx) 
    
    def load_memory(self):
        print("loading memory")
        self.in_memory = []

        for l,loader in enumerate(self.loaderlist):
          
            if len(loader.dataset.fns)==0:    
                print("No Files")
                continue

            for n in range(int(self.n_memory)):
                data,idx = loader.dataset.select_data_from_list()
                self.in_memory.append([data,(l,idx)])

    def search_files(self):

        for l,loader in enumerate(self.loaderlist):
            loader.dataset.search_files()

    def __len__(self):
        return len(self.loaderlist)

class Data_Handler(Dataset):

    def __init__(self):

        ####
        self.fns = []
        self.selection_weights = []
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        #### random selection parameters

        self.random = Random()
        self.segment = Segment()

        ## params for calculating loss 
        self.method = "None"
        self.train_channels = None
        ## Variables for buffering 
        self.use_in_memory = True
        self.in_memory = []
        ## Recombining channels 
        self.remap_label = None 

    def  __getitem__(self, idx):    
                
        ## Clear the buffer after selection?
        #if idx == (len(self)-1): self.in_memory = []   

        if len(self.fns)==0:    
            print("No Files")
            return None

        if self.use_in_memory and len(self.in_memory)>0:
            im_cat, idx = self.in_memory 
        else:
            im_cat,idx = self.select_data_from_list(idx)
            self.in_memory = (im_cat,idx)

        if im_cat is None: return None, idx

        inpt_im, targ_im = self.get_section(im_cat)
        inpt_im = torch.tensor(inpt_im).to(self.device).float()
        targ_im = torch.tensor(targ_im).to(self.device).float()  
        
        return (inpt_im, targ_im, idx) 

    def __len__(self):
        return self.selection_size

    def get_section(self, im_cat):

        use_random = np.random.uniform(0,10)>5

        if im_cat is None: return None

        inpt_im, targ_im = self.random.apply(im_cat)


        if use_random:
            inpt_im = random_noise(inpt_im)
            
        targ_im = self.generate_target(targ_im, inpt_im)

        return inpt_im, targ_im
        
    def generate_target(self, targ_im, inpt_im=None):

        if inpt_im is not None:
            if np.all(inpt_im<0.05) or np.all(inpt_im>0.95):
                return np.zeros_like(targ_im)

        if (targ_im.dtype == np.uint8) and (np.max(targ_im)>5):    
            targ_im = targ_im/255.

        if self.method == "any":
            targ_im = targ_im.max(axis=0,keepdims=True)

        targ_im = self.segment.apply(targ_im)

        if self.remap_label is not None:
            targ_im = relabel(targ_im,self.remap_label)
        return targ_im

    def select_data_from_list(self,idx=0):

        if len(self.fns)==0: return None, idx
        im_cat = None

        while im_cat is None:
            if len(self.fns)==0: break
            idx = self.select(idx)
            fn = self.fns[idx]

            if   self.which == "last":  t = -1
            elif self.which == "first": t = 0
            elif self.which == "all":   t = "random"

            try:
                im_cat = self.load_data(fn,t)
            except:
                im_cat = None

            if im_cat is None: 
                self.fns.pop(idx)
                self.selection_weights = np.delete(self.selection_weights, idx)
                idx = None
                continue

            if im_cat[1].any()==False: 
                self.fns.pop(idx)
                self.selection_weights = np.delete(self.selection_weights, idx)
                idx = None
                im_cat = None
                continue

        return im_cat,idx

    def select(self, idx):
        maxN = len(self.fns)

        if  0:#self.random_selection or idx is None:
            weights = self.selection_weights 
            weights = weights / np.sum(weights)
            idx = np.random.choice( maxN , 1, p = weights)[0]  
        else:
            idx = np.random.choice( maxN , 1)[0]  
        return idx

    def reset_weights(self):
        self.selection_weights = self.selection_weights*0 + 1

    def load_data(self, fn, t):
        return None

class H5_Handler(Data_Handler):

    def __init__(self, fns=None, datasets=None, channels=None, which="all", params=None, search=True):

        super().__init__()

        if datasets is None: datasets = ["image_data", "image_data"]
        if channels is None: channels = [[0],[0]]

        self.search_paths = []
        self.dataset_names = datasets
        self.channels = channels
        self.which = "all"
        self.params = params

        if params is not None:
            for key in params:
                setattr(self, key, params[key])

        if search:
            if fns is not None:
                self.fns = make_file_list( fns, dataset=datasets[0], which=which)
                self.selection_weights = np.ones(len(self.fns))
            else:
                self.search_files()

        if len(self.fns)==0: print("File list is empty")

    
    def search_files(self):

        if len(self.fns)>0: return 

        if len(self.search_paths) == 0: return 

        search_paths = self.search_paths
        datasets = self.dataset_names

        fns = []
        for path in search_paths:

            clean_path = ("/").join(path.split("*")[0].split("/")[:-1])
            if not os.path.isdir(clean_path):
                print(path, "?")
                continue

            x = glob.glob(path +  "/*.h5")
            if len(x)==0: continue
            fns.extend(x)

        self.fns = make_file_list( fns)
        self.selection_weights = np.ones(len(self.fns))

        print( str(len(self.fns)) + " Files Found")

    def load_data(self,fn,t):

        chans = self.channels
        dsets = self.dataset_names 

        if t == "random":
            with h5py.File(fn, 'r') as fh:
                dataset = dsets[0]
                if dataset not in fh: return None
                data = fh[dataset]
                t = np.random.randint(data.shape[0])



        if len(dsets) == 1:  dsets.append(dsets[0])
        ## Load Input
        input_im = read_h5( fn, dataset=dsets[0], t=t, c=chans[0]) 

        if input_im is None: return None
        if input_im.dtype == np.uint8: input_im = input_im/256.
        elif input_im.dtype == bool: input_im = input_im*1.

        ## Check if Input is the same as Target
        if (dsets[0] == dsets[1]) and (chans[0] == chans[1]):
            targt_im = input_im.copy()
            return (input_im, targt_im)
        
        if self.method=="any":
            chans = (None, None)

        ## Load Target
        targt_im = read_h5( fn, dataset=dsets[1], t=t, c=chans[1]) 
        if targt_im is None: ## Maybe do in a smarter way. 
            targt_im = read_h5( fn, dataset=dsets[1], t=0, c=chans[1]) 
            if targt_im is None: return None

        if targt_im.dtype == np.uint8:  
            if targt_im.max()>1:         targt_im = targt_im/256.
        elif targt_im.dtype == bool:  targt_im = targt_im*1.


        ## Set the images to the same size:
        sz_in = input_im[0].shape
        sz_tg = targt_im[0].shape
        if sz_in != sz_tg:
            targt_im = resize_to_base(targt_im, size_out=sz_in)

        return (input_im, targt_im)

class Tiff_Handler(Data_Handler):
    
    def __init__(self, targ_fns):
        
        super().__init__()

        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        inpt_fns = [fns.replace("label","input") for fns in targ_fns]

        self.fns = list(zip(inpt_fns,targ_fns))
        self.selection_weights = np.ones(len(self.fns))
    
    def load_data(self,idx):

        scale = 1
        inpt_fn, trgt_fn = self.fns[idx]

        inpt_im = np.array( io.imread( inpt_fn ))
        inpt_im = inpt_im.transpose((2,0,1))
        inpt_im = inpt_im/256
        inpt_im = resize_to_base( inpt_im, scale)
        
        targ_im = np.array( io.imread( trgt_fn ))
        targ_im = expand_label(targ_im, n_chans=2)
        targ_im = resize_to_base( targ_im, scale)

        return (inpt_im, targ_im)

## Helper Objects
class Random():

    def __init__(self):
        self.selection_size = 5
        self.randomize = True
        self.random_selection = False
        self.scale_range = [.05,.8]
        self.outsize = [500,500]
        self.rand_flip = [0.5,0.5]
        self.force_label = True

    def apply(self, im_cat):

        scale = np.random.uniform(self.scale_range[0], self.scale_range[1])
        out_sz = np.array(self.outsize)
        crop_sz = (out_sz/scale).astype(int)
        im_cat = random_crop(im_cat, crop_sz, self.force_label )
        im_cat = [resize_to_base(im, size_out=out_sz) for im in im_cat]
        im_cat = random_flip(im_cat, self.rand_flip)
        inpt_im, targ_im = im_cat

        if not self.randomize:
            out_sz = np.floor( np.array(im_cat[0].shape[1:3]) / 8)*8
            out_sz = out_sz.astype(int)  
            inpt_im = im_cat[0][:,:out_sz[0],:out_sz[1]]
            targ_im = im_cat[1][:,:out_sz[0],:out_sz[1]]   

        if (inpt_im.dtype == np.uint8):   inpt_im = inpt_im/255.  

        return (inpt_im, targ_im)

class Segment():
        
    def __init__(self):

        ## signal Segmentation params
        self.threshold = None
        self.area_range = None
        self.fill_area = None
        self.imclose = None 
        self.enforce_seperation = False
        
    def apply( self, targ_im ):
    
        if self.threshold is not None:
            targ_im = targ_im > self.threshold

            if self.area_range is not None:
                area_lims = self.area_range
                if len(area_lims)>1:
                    min_size,max_size = area_lims
                else:
                    min_size, max_size = area_lims, None
                targ_im = area_filter(targ_im, min_size=min_size, max_size=max_size)>0

            if self.imclose is not None:
                d = self.imclose                
                targ_im = bi_close(targ_im, radius=d )>0
                #targ_im = ndi.binary_closing(targ_im, structure=np.ones((1,d,d)) )

            if self.fill_area is not None:
                targ_im = area_filter(~targ_im, min_size=self.fill_area )==0

        if self.enforce_seperation:

            t_closed = bi_close(targ_im>0, radius=3 )>0
            targ_im[t_closed & ~(targ_im>0)] = -1
        
        return targ_im

### Helper Functions

def make_file_list(  file_names):

    file_list = []
    for fn in file_names:
        file_list.append( fn )

    return file_list

def read_h5(filename, dataset=None, t=None, c=None ):

    if dataset is None:    dataset = "image_data"

    try:

        with h5py.File(filename, 'r') as fh:

            if dataset not in fh: return None
            data = fh[dataset]

            if data.ndim == 3:           c = None
            elif c is not None: 
                    if np.max(c) >= data.shape[1]: return None
            elif c is None:   c = slice(c)
            elif isinstance(c,list): c = np.array(c)
                
            if data.ndim == 2:           t = None
            elif t == "random": t = np.random.randint(data.shape[0])
            elif t is not None: 
                #if np.max(t) >= data.shape[0]: return None
                if t >= data.shape[0]: return None
            elif t is None:   t = slice(None)
            
            
            x,y = check_active(data)

            if  data.ndim == 4:          
                data = np.array(data[t,c,x,y])
            elif data.ndim == 3:          
                data = np.array(data[t,x,y])
            elif data.ndim == 2:           
                data = np.array(data[x,y])

            #if data.ndim == 3:           data = data[None,...]
            if data.ndim == 2:           data = data[None,:,:]
    
    except:
        return None

    ## Extra steps
    data = subsample(data)
    data = data.astype(np.uint8)

    return data

def check_active(data):

    if data.attrs.__contains__("active"):
        x1,x2,y1,y2 = data.attrs['active']
        x,y = slice(x1,x2), slice(y1,y2)
    else:
        x,y = slice(None), slice(None)

    return x,y

def subsample(im_data):

    dims = np.array(im_data[0].shape)
    sz = int(dims.min()/4)
    x,y = (dims/2).astype(int)
    x1,x2,y1,y2 = x-sz, x+sz, y-sz, y+sz

    if np.any(dims > 6000):  
        #print("Cropping:" ,dims, end="" )
        #im_data = im_data[:,:,x1:x2,y1:y2]  
        #im_data = resize_dataset(im_data, scale=0.5)
        im_data = im_data[:,::2,::2]

    elif np.any(dims > 4000):   
        #print("Reducing1:",dims , end="-" )
        im_data = im_data[:,x1:x2,y1:y2]  
        #im_data = resize_dataset(im_data, scale=0.5)
        im_data = im_data[:,::2,::2]

    elif np.any(dims > 2500):
        pass
        
    return im_data

def expand_label(label_in, n_chans=None ):
    
    if n_chans is None:
        n_chans = label_in.max()
    label_im = np.stack([1.0*(label_in==i) for i in range(1,n_chans+1)])

    return label_im

def relabel(im,vmap):

    im = im.transpose(1,2,0)
    im = np.dot(im,vmap)
    im = im.transpose(2,0,1)
    return im 
    
def resize_to_base(im_in, reduction=None, base=1, size_out=None):
    
    if reduction==1:
        return im_in

    if size_out is None:
        sz_out = np.array(im_in.shape[1:3])[::-1] * reduction 
        sz_out = np.round(sz_out/base)*base
    else:
        if len(size_out)==3: size_out = size_out[1:]
        sz_out = size_out[::-1]
        sz_out = np.array(sz_out)

    #im_in = im_in.astype(np.uint8)
    sz_out = sz_out.astype(int)
    
    im_out = np.stack( [cv2.resize(im, tuple(sz_out)) for im in im_in ])
    return im_out

def random_crop(im, out_size, force_label=False):

    attempts = 1

    sz = np.array(im[0].shape)

    start = sz[1:3] - out_size + [1,1]
    start = np.maximum(start,1)
    for _ in range(attempts):
                
        s1 = np.random.randint(start[0])-1
        s2 = np.random.randint(start[1])-1
        start1 = np.maximum([s1,s2],0)
        end = (start1 + out_size).astype(int)

        im_crop = im[1][:,start1[0]:end[0], start1[1]:end[1]]

        if np.std(im_crop) < 0.001: continue
        ## Crop Label and check to see if there is anything there
        lb_crop = im[1][:,start1[0]:end[0], start1[1]:end[1]]

        #if (not force_label) or np.any(lb_crop): break
        
        if (not force_label) or (lb_crop).max(axis=0).mean() > 0.01 : break

    in_crop = im[0][:,start1[0]:end[0], start1[1]:end[1]]
    lb_crop = im[1][:,start1[0]:end[0], start1[1]:end[1]]
        
    im_out = (in_crop,lb_crop)
    return im_out

def random_flip(im_cat,prob):
    
    rand_var = np.random.rand(2)
    if rand_var[0]< prob[0]:
        im_cat = [ np.flip(im,axis=2) for im in im_cat]
    if rand_var[1]< prob[1]:
        im_cat = [ np.flip(im,axis=1) for im in im_cat]

    im_cat = [ im-0 for im in im_cat] 

    return im_cat

##########################

def random_noise(im):

    #if np.random.randint(2)==0:  im = 1 - im
    if np.random.uniform(0,1)<0.1: im = 1-im

    x = np.random.uniform(1,4, 1)
    if np.random.uniform(0,1)<0.5: x = 1/x
    mu = im.mean()
    im = ((im-mu)*x)+mu    

    x = np.random.uniform(-0.2,0.2,1)
    im = im + x    

    size = im.shape
    x = np.random.normal(0, 0.4, size)*np.random.uniform(-0.2, 0.2, 1)
    im = im + x
    im = im.clip(0,1)

    if np.random.uniform(0,1)>0.1: 
        for i in range(3):
            x = np.random.randint(1,im.shape[1]-20)
            w = np.random.randint(3,15)
            im[0,x:(x+w),:] = np.random.uniform(0,1)

            y = np.random.randint(1,im.shape[2]-20)
            w = np.random.randint(3,15)
            im[0,:,y:(y+w)] = np.random.uniform(0,1)

            x = np.random.randint(1,im.shape[1]-20)
            y = np.random.randint(1,im.shape[2]-20)
            w = np.random.randint(10,20)

            im[0,x:(x+w),y:(y+w)] = np.random.uniform(0,1)

    if np.random.uniform(0,1)>0.1:
        im = ndi.gaussian_filter(im,np.random.randint(2,10))

    return im 

def bi_close(  bI,radius,down_sample=None):

    if down_sample is None:
        down_sample = minimum((5 / radius),1)
    
    im_size = np.array( bI.shape )

    d_size = (im_size*down_sample).astype(int)
    radius = int(radius * down_sample)

    filt = resize_to_base((bI*1.),  size_out=d_size) > 0.5
    dsk = mor.disk(radius)[None,:]
    filt = ndi.binary_closing( filt, dsk)
    bI_out = resize_to_base((filt*1.),  size_out=im_size) > 0.5

    return bI_out

def area_filter(ar, min_size=0, max_size=None):
    """
    """
    if ar.dtype == bool:
        ccs,l_max = ndi.label(ar)
    else:
        ccs = ar
        l_max = ar.max()

    component_sizes = np.bincount(ccs[ccs>0])
    idxs = np.arange(l_max+1).astype(np.uint16)
    if min_size>0:
        too_small = component_sizes < min_size
        idxs[too_small]=0

    if max_size is not None:
        too_large = component_sizes > max_size
        idxs[too_large]=0

    out = np.zeros_like(ccs, np.uint16)
    _, idxs2 = np.unique(idxs,return_inverse=True)
    out[ccs>0] = idxs2[ccs[ccs>0]]

    return out