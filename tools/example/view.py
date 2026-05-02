
import numpy as np 
import matplotlib.pyplot as plt
from .loss import *
import torch
import os
###########################
def show_output(inputs,labels,pred,axs=None):
    im = np.array(inputs.cpu())[0].transpose([1,2,0])
    lb = np.array(labels.cpu())[0].transpose([1,2,0])
    pd = np.array(pred.cpu().detach())[0].transpose([1,2,0]) 

    clrmap = np.array([[1,0,0],[0,1,0],[0,0,1],[1,1,0],[1,0,1],[0,1,1]])

    if   pd.shape[2] == 1:     pd = np.tile(pd,(1,1,3))
    elif pd.shape[2] == 3:     pd = pd
    else: pd = np.dot(pd, clrmap[:pd.shape[2]])
    lb = lb*255

    if   lb.shape[2] == 1:     lb = np.tile(lb,(1,1,3))
    elif lb.shape[2] == 3:     lb = lb
    else: lb = np.dot(lb, clrmap[:lb.shape[2]])

    pd = pd*255

    if axs is None:
        fig = plt.figure(figsize=(14,10))
    axs = [[] for i in range(4)]

    axs[0] = plt.subplot(2,3,1)
    axs[1] = plt.subplot(2,3,2)
    axs[2] = plt.subplot(2,3,3)
    axs[3] = plt.subplot(2,1,2)

    axs[0].imshow(im)
    axs[1].imshow(lb)
    axs[2].imshow(pd)    

    axs[0].set_title("Input Image")
    axs[1].set_title("Ground Truth")
    axs[2].set_title("Model Prediction")

    axs[3].set_title("Model Prediction: Individual Channels")
    #axs[1].imshow(np.argmax(lb,axis=2))
    #axs[2].imshow(np.argmax(op,axis=2))    
    axs[3].imshow(pd)

    plt.subplots_adjust(left=0.05, bottom=0.05, right=0.95, top=0.95, wspace=.05, hspace=.05)
    plt.show()
    
###########################
def print_mosaic_x(Loader,model=None, m=5,n=5, save=False):
        
    samples = get_sample_tiles(Loader,model, n_tiles=m*n)
    samp_im = stack_sample_mosiac(samples, n=n )

    samp_im = samp_im.clip(0,1)

    plt.close("all")
    fig = plt.figure(figsize=(10,8))
    fig.subplots_adjust(left=.01, bottom=.05, 
                right=.98, top=.98, wspace=0, hspace=0)
    plt.imshow(samp_im)

    if save:
        fn = "output/sample_tiles.jpg"
        plt.imsave( fn, samp_im)

def print_mosaic(Loader,model=None, m=5,n=5, save=False):

    Loader.dataset.in_memory = []
    Loader.dataset.randomize = True
    Loader.dataset.random_selection = False
    Loader.dataset.selection_size = 1
    Loader.batchsize = 1
    Loader.device="cpu"

    if model is not None:  model = model.to("cpu")

    samples = get_sample_tiles(Loader,model, n_tiles=m*n)

    if len(samples)==0: return
    samp_im = stack_sample_mosiac(samples, n=n )

    plt.close("all")
    fig = plt.figure(figsize=(10,8))

    fig.subplots_adjust(left=.01, bottom=.05, 
                right=.98, top=.98, wspace=0, hspace=0)
    plt.imshow(samp_im)

    if save:
        fn = "output/sample_tiles.jpg"
        plt.imsave( fn, samp_im)

def get_sample_tiles(Loader,model=None, n_tiles=5):
    
    samples = []
    n = 0 

    for _ in range(n_tiles):
        #Loader.dataset.in_memory = []
        for X in Loader:

            idx = 0 
            if len(X)==3: 
                inpt,targ,idx = X
                #print(Loader.dataset.fns[idx][0][-100:])
            elif len(X)==2: inpt,targ = X

            if len(inpt)==0: continue
            
            I = colorize_channels(inpt)
            T = colorize_channels(targ)

            tile = np.hstack((I,T))

            n = n+1
            if n>n_tiles: break 

            print(np.array(idx),end="\t")

            if model is not None:
                pred = model(inpt)
                pred[pred<0.3] = 0

                loss = np.array(calc_loss(pred, targ).detach())
                print(np.round(loss,3))
                P = colorize_channels(pred)
                tile = np.hstack((tile,P))

            samples.append(tile)

        if n>n_tiles: break 
        
    
    samples = samples[:n_tiles]
    return samples

def stack_sample_mosiac(samples,n=5):

    ## to do, fix using reshape 
    total_size =  len(samples)
    samp_im = np.vstack(samples[0:n]) 

    for i in range(1,int(total_size/n)):

        samp2 = np.vstack(samples[(i*n):((i+1)*n)])       
        samp_im = np.hstack((samp_im, samp2 ))  

    return samp_im    

def colorize_channels(I, clrmap=None):

    if clrmap is None:
        clrmap = np.array([[1,0,0],[0,1,0],[0,0,1],[0.7,0.7,0],[1,0,1],[0,1,1]])

    if isinstance(I, torch.Tensor):  I = I.cpu().detach().numpy()

    if I.dtype==np.uint8: I = I/255.
    if I.ndim == 4: I = I[0]

    I = I.transpose(1,2,0)
    if   I.shape[2] == 1:     T = np.tile(I,(1,1,3))
    elif I.shape[2] == 3:     T = I
    else: T = np.dot(I, clrmap[:I.shape[2]])

    T = T.clip(0,1)
    #T = (T*255).astype(np.uint8)

    return T

############################
def print_comparisons_x(Loader,model=None,n=10):

    plt.close("all")    
    
    model = model.to("cpu")
    
    for X in Loader:
        if len(X)==3: 
            inpt,targ,idx = X
        elif len(X)==2: inpt,targ = X

        pred = None
        if model is not None: 
            pred = model(inpt)
        show_comparison( inpt, targ, pred)

def print_comparisons(Loader,model=None, n_samples=5, save=False, out_dir=""):

    plt.close("all") 
    ################## Remove turn into object method or something   
    Loader.dataset.selection_size = n_samples
    Loader.dataset.random_selection = False
    Loader.dataset.randomize = False

    Loader.dataset.device="cpu"
    model = model.to("cpu")

    num = 0
    for X in Loader:
        if len(X)==3: 
            inpt,targ,idx =X
            print(np.array(idx))
        elif len(X)==2: inpt,targ =X
        
        Loader.dataset.in_memory = []        
        
        pred = None
        if model is not None: 
            #pred = model.segment(inpt,0.5)
            pred = model(inpt)

        fig = show_comparison( inpt, targ, pred)

        if save:

            os.makedirs(out_dir, exist_ok = True)
            name = Loader.dataset.fns[idx][0]
            name = name.split("\\")[-1].replace(".H5","")            

            im_out = get_frame(fig)
            fn = out_dir + "/"+ str(num) +"_" +name+ ".jpg"
            plt.imsave( fn, im_out)
            num = num+1

            plt.close(fig)

def show_comparison( inpt, targ, pred=None):

    I = colorize_channels(inpt)
    T = colorize_channels(targ)

    tile = np.hstack((I,T))

    if pred is not None:
        print(calc_loss(pred, targ))
        P = colorize_channels(pred)
        tile = np.hstack((tile,P))

    ###################################
    
    fig,ax = plt.subplots(1,1,figsize=(16,8))
    plt.subplots_adjust(left=0.05, bottom=0.05, right=0.95, top=0.85,
                        wspace = 0.1, hspace = 0.2 )
    ax.imshow(tile)
    plt.draw()
    #plt.title("dice coefficent: " + str(dice))
    return fig
       
############################

def draw_model_params(model):
    
    Model_Parameters =  [param for param in model.parameters()]

    weights = Model_Parameters[::2]
    bias =  Model_Parameters[1::2]
    draw_v_weights(weights,bias)

def draw_v_weights(weights,bias):

    num_rows = int(np.ceil(len(weights)-1)/2)

    fig, axs = plt.subplots(num_rows,2)
    fig2, axs2 = plt.subplots(num_rows,2)
    
    plt.tight_layout()
    fig.subplots_adjust(left=.05, bottom=.05, 
                right=.8, top=.95, 
                wspace=.1, hspace=.1)
    
    fig2.subplots_adjust(left=.05, bottom=.05, 
                right=.8, top=.95, 
                wspace=.1, hspace=.1)

    for n in range(num_rows*2):

        ax1 = axs.ravel()[n]
        w = weights[n].detach().cpu().numpy()
        w = np.abs(w).max(axis=(2,3)).T
        ax1.imshow(w)
        ax1.axis("tight")       

        ax2 = axs2.ravel()[n]
        
        b = bias[n].detach().cpu().numpy()
        ax2.imshow(b[:,None].T)
        ax2.axis("tight")

    w = weights[-1].detach().cpu().numpy()
    w = np.abs(w).max(axis=(2,3)).T
    ax = fig.add_axes([0.82,0.05,0.15,0.9])
    ax.imshow(w)
    ax.axis("tight")

    b = bias[-1].detach().cpu().numpy()
    b = b[:,None].T
    ax = fig2.add_axes([0.82,0.05,0.15,0.9])
    ax.imshow(b)

def draw_weights(weights,bias):

    n = int(np.ceil(len(weights)/2))

    fig, axs = plt.subplots(n,2)
    fig2, axs2 = plt.subplots(n,2)
    
    plt.tight_layout()
    fig.subplots_adjust(left=.05, bottom=.05, 
                right=.95, top=.95, 
                wspace=.1, hspace=.1)
    
    fig2.subplots_adjust(left=.05, bottom=.05, 
                right=.95, top=.95, 
                wspace=.1, hspace=.1)

    i,j = 0,0
    for c,w in enumerate(weights):
        w = w.detach().cpu().numpy()
        w = np.abs(w).max(axis=(2,3)).T
        
        b = bias[c].detach().cpu().numpy()
        
        axs[i,j].imshow(w)
        axs[i,j].axis("tight")
        axs2[i,j].imshow(b[:,None].T)
        #axs2[i,j].axis("scaled")

        if c == n-1:
            j = 1
        elif (c > n-1):
            i -= 1
        else:
            i+=1

#############################

def get_frame(fig):
    fig.canvas.draw()
    w,h = fig.canvas.get_width_height()
    buf = np.frombuffer ( fig.canvas.tostring_argb(), dtype=np.uint8 )
    im = buf.reshape(h,w,4)[:,:,1:]
    return im